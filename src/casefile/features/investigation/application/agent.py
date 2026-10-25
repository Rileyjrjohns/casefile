"""Checkpointed investigation execution with reservations and human-review handoff."""

import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from casefile.features.investigation.application.agent_policy import (
    candidates,
    risk_inputs,
    validate_analysis,
)
from casefile.features.investigation.application.agent_types import (
    AgentCodec,
    CaseContext,
    EvidenceBatch,
    EvidenceGateway,
    ExecutionJournal,
    ExecutionSnapshot,
    InvestigationContext,
    LanguageModel,
    ModelRequest,
    RecordedCallFailure,
    Signal,
    SourceLink,
    Telemetry,
    TraceEvent,
    tool_input,
)
from casefile.features.investigation.application.banking_policy import BANKING_KINDS, is_concern
from casefile.features.investigation.application.decisions import (
    DecisionGateway,
    DecisionRequest,
    DecisionResponse,
    state_json,
)
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    FINDINGS_VERSIONS,
    LEGACY_FINDINGS,
)
from casefile.features.investigation.application.guard_policy import (
    guard_denial_reasons,
    guard_state,
)
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.recommendation_policy import constrain_closure
from casefile.features.investigation.application.tools import (
    CaseDocument,
    InvalidToolQuery,
    InvestigatorNote,
    KycProfile,
    ToolName,
    customer_query,
    sanction_query,
)
from casefile.features.investigation.domain.analysis import Recommendation, SourceReference
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.clock import Clock
from casefile.shared.result import Err, Ok, Result


def checked[T, E](result: Result[T, E]) -> T:
    if isinstance(result, Err):
        raise RuntimeError(f"Execution contract violated: {result.error}")
    return result.value


@dataclass(frozen=True, slots=True)
class InvestigationAgent:
    journal: ExecutionJournal
    contexts: InvestigationContext
    tools: EvidenceGateway
    model: LanguageModel
    codec: AgentCodec
    clock: Clock
    telemetry: Telemetry
    decisions: DecisionGateway
    risk_enabled: bool = False
    alternative_models: Mapping[str, LanguageModel] = field(default_factory=dict)
    findings_policy: str = CURRENT_FINDINGS

    def __post_init__(self) -> None:
        if self.findings_policy not in FINDINGS_VERSIONS:
            raise ValueError("Unknown findings policy version.")

    async def execute(self, lease: JobLease) -> None:
        with self.telemetry.span(
            "investigation.run", {"run.id": str(lease.run_id), "attempt": lease.attempt}
        ):
            current = await self.journal.load(lease.run_id)
            if current is None:
                raise RuntimeError("Queued run does not exist.")
            if current.snapshot.run.state in {
                RunState.AWAITING_REVIEW,
                RunState.CLOSED,
                RunState.FAILED,
            }:
                return
            await _Execution(self, lease, current).execute()


@dataclass
class _Execution:
    agent: InvestigationAgent
    lease: JobLease
    current: ExecutionSnapshot

    @property
    def run(self) -> Run:
        return self.current.snapshot.run

    def event(
        self,
        operation: str,
        phase: Literal["started", "succeeded", "failed", "transition"],
    ) -> TraceEvent:
        return TraceEvent(len(self.current.events) + 1, operation, phase, self.agent.clock.now())

    async def save(self, run: Run, event: TraceEvent) -> None:
        self.current = checked(
            await self.agent.journal.checkpoint(
                self.lease,
                run,
                event,
                expected_revision=self.current.snapshot.revision,
            )
        )
        self.agent.telemetry.event(
            "run.checkpoint",
            {
                "run.id": str(run.id),
                "sequence": event.sequence,
                "operation": event.operation,
                "phase": event.phase,
                "state": run.state.value,
            },
        )

    async def fail(self, reason: str) -> None:
        await self.save(
            checked(self.run.fail(reason)),
            replace(self.event("execution.failed", "failed"), error=reason),
        )

    async def perform(
        self,
        operation: str,
        input_json: str,
        reservation: BudgetUsage,
        invoke: Callable[[], Awaitable[Result[str, InvalidToolQuery]]],
        actual_usage: Callable[[str], BudgetUsage],
        sources: Callable[[str], tuple[SourceReference, ...]] | None = None,
    ) -> str | None:
        previous = next(
            (event for event in reversed(self.current.events) if event.operation == operation), None
        )
        if previous and previous.phase == "succeeded":
            if previous.input_json != input_json or previous.output_json is None:
                await self.fail("Stored execution request no longer matches the current plan.")
                return None
            return previous.output_json
        reserved_run = checked(self.run.reserve(reservation))
        if reserved_run.state == RunState.FAILED:
            await self.save(
                reserved_run,
                replace(
                    self.event(operation, "failed"),
                    input_json=input_json,
                    reserved=reservation,
                    error=reserved_run.failure_reason,
                ),
            )
            return None
        await self.save(
            reserved_run,
            replace(
                self.event(operation, "started"),
                input_json=input_json,
                reserved=reservation,
            ),
        )
        try:
            with self.agent.telemetry.span(operation, {"run.id": str(self.run.id)}):
                result = await invoke()
                if isinstance(result, Err):
                    await self.save(
                        checked(self.run.fail(result.error.message)),
                        replace(
                            self.event(operation, "failed"),
                            input_json=input_json,
                            reserved=reservation,
                            error=f"query:{result.error.message}",
                        ),
                    )
                    return None
                usage = actual_usage(result.value)
                updated = checked(self.run.settle(reservation, usage))
                if sources is not None and updated.state != RunState.FAILED:
                    updated = checked(updated.record_sources(sources(result.value)))
        except Exception as error:
            # Preserve the reservation when the provider's actual usage is unknown.
            await self.save(
                self.run,
                replace(
                    self.event(operation, "failed"),
                    input_json=input_json,
                    reserved=reservation,
                    error=error.code
                    if isinstance(error, RecordedCallFailure)
                    else type(error).__name__,
                ),
            )
            raise
        # A storage failure here leaves STARTED: the next owner must not repeat an uncertain call.
        await self.save(
            updated,
            replace(
                self.event(operation, "succeeded"),
                input_json=input_json,
                output_json=result.value,
                reserved=reservation,
                actual=usage,
            ),
        )
        return result.value if updated.state != RunState.FAILED else None

    async def tool(
        self,
        tool: ToolName,
        query: str,
        context: CaseContext,
        evidence: Sequence[EvidenceBatch],
    ) -> EvidenceBatch | None:
        authorized = sanction_query(query) if tool == ToolName.SANCTIONS else customer_query(query)
        if isinstance(authorized, Err):
            await self.fail(f"query:{authorized.error.message}")
            return None
        facts = guard_state(tool, query, context, evidence)
        guard = await self.decision(
            DecisionRequest(
                tool.value,
                "probability",
                "guard",
                state_json(facts),
            )
        )
        if guard is None:
            return None
        if float(guard.value) < 0.8:
            reasons = guard_denial_reasons(facts) or ("provider verdict below threshold",)
            await self.fail(f"Pre-tool decision guard denied the query: {'; '.join(reasons)}.")
            return None
        reservation = checked(BudgetUsage.create(1, 0, Decimal(0)))

        async def invoke() -> Result[str, InvalidToolQuery]:
            result = await self.agent.tools.fetch(tool, query)
            if isinstance(result, Err):
                return result
            if result.value.tool != tool or result.value.query != query:
                raise ValueError("Evidence does not match the requested tool and query.")
            return Ok(self.agent.codec.evidence_json(result.value))

        def sources(payload: str) -> tuple[SourceReference, ...]:
            batch = self.agent.codec.evidence(payload)
            return tuple(
                checked(SourceReference.create(tool, record.record_id)) for record in batch.records
            )

        payload = await self.perform(
            f"tool.{tool}",
            tool_input(tool, query),
            reservation,
            invoke,
            lambda _: reservation,
            sources,
        )
        return self.agent.codec.evidence(payload) if payload is not None else None

    async def decision(self, request: DecisionRequest) -> DecisionResponse | None:
        async def invoke() -> Result[str, InvalidToolQuery]:
            response = await self.agent.decisions.execute(request)
            return Ok(self.agent.codec.decision_response_json(response))

        def usage(payload: str) -> BudgetUsage:
            response = self.agent.codec.decision_response(payload)
            return checked(BudgetUsage.create(0, response.tokens, response.cost))

        def sources(payload: str) -> tuple[SourceReference, ...]:
            response = self.agent.codec.decision_response(payload)
            if not response.model.startswith("risk_model@"):
                raise ValueError("Tabular risk must identify the versioned local model.")
            return (checked(SourceReference.create(response.model, request.key)),)

        operation = f"decision.{request.purpose}.{request.key}"
        previous = next(
            (e for e in reversed(self.current.events) if e.operation == operation), None
        )
        reservation = (
            previous.reserved
            if previous and previous.phase == "succeeded" and previous.reserved is not None
            else self.agent.decisions.quote(request)
        )
        payload = await self.perform(
            operation,
            self.agent.codec.decision_request_json(request),
            reservation,
            invoke,
            usage,
            sources if request.purpose == "risk" else None,
        )
        if payload is None:
            return None
        response = self.agent.codec.decision_response(payload)
        valid = math.isfinite(response.confidence) and 0 <= response.confidence <= 1
        if request.operation == "choose":
            valid = valid and isinstance(response.value, str) and response.value in request.options
        else:
            maximum = len(request.options) - 1 if request.operation == "score" else 1
            valid = (
                valid
                and isinstance(response.value, float)
                and math.isfinite(response.value)
                and 0 <= response.value <= maximum
            )
        if not valid:
            await self.fail("Decision provider returned an invalid typed answer.")
            return None
        return response

    async def constrain_recommendation(
        self,
        proposed: Recommendation,
        signals: tuple[Signal, ...],
        *,
        has_profile: bool,
        evidence_gaps: bool,
    ) -> Recommendation | None:
        constrained = constrain_closure(
            proposed, signals, has_profile=has_profile, evidence_gaps=evidence_gaps
        )
        if constrained.recommendation == proposed:
            return proposed
        operation = "policy.recommendation.v1"
        input_json = state_json(
            {
                "proposed": proposed.value,
                "blocking_signals": constrained.blocking_signals,
                "has_profile": has_profile,
                "evidence_gaps": evidence_gaps,
            }
        )
        output_json = state_json(
            {"recommendation": constrained.recommendation.value, "reasons": constrained.reasons}
        )
        previous = next((e for e in self.current.events if e.operation == operation), None)
        if previous is not None:
            if previous.input_json != input_json or previous.output_json != output_json:
                await self.fail("Stored recommendation constraint no longer matches the evidence.")
                return None
        else:
            await self.save(
                self.run,
                replace(
                    self.event(operation, "transition"),
                    input_json=input_json,
                    output_json=output_json,
                ),
            )
        return constrained.recommendation

    async def execute(self) -> None:
        latest = {event.operation: event for event in self.current.events}
        if any(event.phase == "started" for event in latest.values()):
            await self.fail(
                "Interrupted external call has an unknown outcome; manual restart required."
            )
            return
        context_event = latest.get("case_context")
        policy_event = latest.get("policy.findings")
        findings_policy = self.agent.findings_policy
        if policy_event is not None:
            recorded_policy = json.loads(policy_event.output_json or "{}").get("version")
            if recorded_policy not in FINDINGS_VERSIONS:
                await self.fail("Stored findings policy version is invalid.")
                return
            findings_policy = str(recorded_policy)
        elif context_event is not None:
            findings_policy = LEGACY_FINDINGS
        else:
            await self.save(
                self.run,
                replace(
                    self.event("policy.findings", "transition"),
                    output_json=state_json({"version": findings_policy}),
                ),
            )
        if context_event is None:
            context = await self.agent.contexts.get(self.run.case_id)
            if context is None or self.run.state != RunState.PENDING:
                await self.fail("Investigation context is missing.")
                return
            await self.save(
                checked(self.run.start_gathering()),
                replace(
                    self.event("case_context", "transition"),
                    output_json=self.agent.codec.context_json(context),
                ),
            )
        else:
            if context_event.output_json is None:
                raise ValueError("Stored case context is missing.")
            context = self.agent.codec.context(context_event.output_json)
        triage = {"kind": context.kind, "description": context.description}
        banking_kind = context.kind if context.kind in BANKING_KINDS else ""
        priority = await self.decision(
            DecisionRequest("alert", "choose", "priority", state_json(triage), ("normal", "high"))
        )
        if priority is None:
            return
        alert_type = await self.decision(
            DecisionRequest(
                "alert",
                "choose",
                "alert_type",
                state_json(triage),
                tuple(sorted(BANKING_KINDS))
                if banking_kind
                else ("unusual_transaction", "sanctions_match", "kyc_inconsistency"),
            )
        )
        if alert_type is None:
            return
        context = replace(context, kind=str(alert_type.value))
        evidence: list[EvidenceBatch] = []
        pending = [ToolName.KYC, ToolName.TRANSACTIONS, ToolName.ALERTS, ToolName.NOTES]
        if banking_kind:
            pending.append(ToolName.DOCUMENTS)
        profile: KycProfile | None = None
        injections: set[str] = set()
        while pending:
            route = await self.decision(
                DecisionRequest(
                    str(len(evidence)),
                    "choose",
                    "tool_route",
                    state_json(
                        {"available": [t.value for t in pending], "priority": priority.value}
                    ),
                    tuple(t.value for t in pending),
                )
            )
            if route is None:
                return
            tool = ToolName(str(route.value))
            query = (
                profile.legal_name if tool == ToolName.SANCTIONS and profile else context.subject_id
            )
            batch = await self.tool(tool, query, context, evidence)
            if batch is None:
                return
            evidence.append(batch)
            pending.remove(tool)
            if tool == ToolName.KYC:
                profile = next((r for r in batch.records if isinstance(r, KycProfile)), None)
                if profile:
                    pending.append(ToolName.SANCTIONS)
            injection = await self.decision(
                DecisionRequest(
                    tool.value,
                    "probability",
                    "injection",
                    state_json({"text": self.agent.codec.evidence_json(batch)}),
                )
            )
            if injection is None:
                return
            if float(injection.value) >= 0.5:
                # Retain evidence as data; flag affected records without executing their contents.
                for record in batch.records:
                    if isinstance(record, (InvestigatorNote, CaseDocument)):
                        detail = await self.decision(
                            DecisionRequest(
                                record.record_id,
                                "probability",
                                "injection",
                                state_json(
                                    {
                                        "text": record.text
                                        + " "
                                        + " ".join(field.value for field in record.fields)
                                        if isinstance(record, CaseDocument)
                                        else record.text
                                    }
                                ),
                            )
                        )
                        if detail is None:
                            return
                        if float(detail.value) >= 0.5:
                            injections.add(record.record_id)
        signals: list[Signal] = []
        for signal in candidates(
            tuple(evidence),
            frozenset(injections),
            kind=banking_kind,
            policy_version=findings_policy,
        ):
            score = await self.decision(
                DecisionRequest(
                    signal.key,
                    "score",
                    "signal",
                    state_json(
                        {
                            "signal_kind": signal.key.split(":")[0],
                            "statement": signal.statement,
                            "evidence": [self.agent.codec.evidence_json(b) for b in evidence],
                        }
                    ),
                    ("unsupported", "fully supported"),
                )
            )
            if score is None:
                return
            signals.append(replace(signal, confidence=float(score.value)))
        concern_count = sum(is_concern(signal) for signal in signals)
        if self.agent.risk_enabled and banking_kind not in {
            "credit_document_fraud",
            "sanctions_identity_review",
        }:
            for record_id, features in risk_inputs(
                tuple(evidence), eur_only=findings_policy == CURRENT_FINDINGS
            ):
                risk = await self.decision(
                    DecisionRequest(record_id, "probability", "risk", features)
                )
                if risk is None:
                    return
                probability = float(risk.value)
                signals.append(
                    Signal(
                        f"risk:{record_id}",
                        f"Synthetic transaction risk estimate for {record_id}: {probability:.1%}. "
                        "This is not a real-world fraud probability.",
                        probability,
                        (
                            SourceLink(risk.model, record_id),
                            SourceLink(ToolName.TRANSACTIONS, record_id),
                        ),
                    )
                )
                concern_count += probability >= 0.5
        if self.run.state == RunState.GATHERING:
            await self.save(
                checked(self.run.start_analysis()), self.event("analyzing", "transition")
            )
        recommendation_result = await self.decision(
            DecisionRequest(
                "final",
                "choose",
                "recommendation",
                state_json(
                    {
                        "signal_count": concern_count,
                        "has_profile": profile is not None,
                        **(
                            {
                                "evidence_gaps": any(s.key.startswith("gap:") for s in signals)
                                or bool(banking_kind and not signals),
                                "workflow": banking_kind or context.kind,
                            }
                            if banking_kind or findings_policy == CURRENT_FINDINGS
                            else {}
                        ),
                        "signals": [
                            {"key": s.key, "support": s.confidence, "statement": s.statement}
                            for s in signals
                        ],
                    }
                ),
                ("close", "escalate", "request_information"),
            )
        )
        if recommendation_result is None:
            return
        recommendation = await self.constrain_recommendation(
            Recommendation(str(recommendation_result.value)),
            tuple(signals),
            has_profile=profile is not None,
            evidence_gaps=any(s.key.startswith("gap:") for s in signals)
            or bool(banking_kind and not signals),
        )
        if recommendation is None:
            return
        route_model = await self.decision(
            DecisionRequest(
                "narrative",
                "choose",
                "model_route",
                state_json({"priority": priority.value, "signal_count": len(signals)}),
                ("primary", *sorted(self.agent.alternative_models)),
            )
        )
        if route_model is None:
            return
        selected_model = (
            self.agent.model
            if route_model.value == "primary"
            else self.agent.alternative_models[str(route_model.value)]
        )
        request = ModelRequest(
            context,
            tuple(evidence),
            tuple(signals),
            max_output_tokens=1024 if route_model.value == "primary" else 512,
        )

        async def generate() -> Result[str, InvalidToolQuery]:
            response = await selected_model.generate(request)
            return Ok(self.agent.codec.response_json(response))

        def usage(payload: str) -> BudgetUsage:
            response = self.agent.codec.response(payload)
            return checked(
                BudgetUsage.create(
                    0, response.input_tokens + response.output_tokens, response.estimated_cost
                )
            )

        payload = await self.perform(
            "model.generate",
            self.agent.codec.request_json(request),
            selected_model.quote(request),
            generate,
            usage,
        )
        if payload is None:
            return
        response = self.agent.codec.response(payload)
        if response.output_tokens > request.max_output_tokens:
            await self.fail("Model exceeded the authorized output token limit.")
            return
        analysis = validate_analysis(
            self.run.id, tuple(signals), recommendation, response, self.run.sources
        )
        if isinstance(analysis, Err):
            await self.fail(analysis.error.message)
            return
        await self.save(
            checked(self.run.submit_analysis(analysis.value)),
            self.event("awaiting_review", "transition"),
        )
