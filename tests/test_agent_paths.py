"""Observable choices of one investigation: what is asked of the decision port and the model."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from casefile.bootstrap.memory import MemoryApplication
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.tools import TypedEvidenceGateway
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import _Execution
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    EvidenceBatch,
    ModelRequest,
    ModelResponse,
    TraceEvent,
)
from casefile.features.investigation.application.decisions import (
    DecisionRequest,
    DecisionResponse,
    state_json,
)
from casefile.features.investigation.application.findings_policy import LEGACY_FINDINGS
from casefile.features.investigation.application.tools import (
    InvalidToolQuery,
    ToolName,
    Transaction,
)
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.ids import CaseId
from casefile.shared.result import Ok, Result
from tests.contracts.support import FixedClock, value
from tests.test_agent_execution import OverridingDecisions, agent_for
from tests.test_agent_workflow import RUN, create_run

TOOLS = {tool.value for tool in ToolName}


def state(request: DecisionRequest) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(request.state_json)
    return decoded


def changing(
    matches: Callable[[DecisionRequest], bool], new_value: str | float
) -> OverridingDecisions:
    return OverridingDecisions(
        lambda request, response: (
            replace(response, value=new_value) if matches(request) else response
        )
    )


async def execute(
    app: MemoryApplication, decisions: OverridingDecisions, **options: Any
) -> tuple[Run, tuple[TraceEvent, ...]]:
    await RunWorker(app.queue, agent_for(app, decisions, **options)).run_once()
    current = await app.journal.load(RUN)
    assert current is not None
    return current.snapshot.run, current.events


def banking_tools(*, without_documents: bool = False) -> TypedEvidenceGateway:
    seed = load_seed(banking=True)
    if without_documents:
        seed = seed.model_copy(update={"documents": ()})
    return TypedEvidenceGateway(MemoryInvestigationTools(seed))


class NoContext:
    async def get(self, case_id: CaseId) -> CaseContext | None:
        return None


@pytest.mark.asyncio
async def test_a_case_without_a_stored_context_fails_the_run() -> None:
    app = await create_run()
    run, _ = await execute(app, OverridingDecisions(), contexts=NoContext())
    assert run.state == RunState.FAILED
    assert run.failure_reason == "Investigation context is missing."


@pytest.mark.asyncio
async def test_a_started_run_without_its_recorded_context_fails_instead_of_guessing() -> None:
    app = await create_run()
    lease = await app.queue.claim(lease_for=timedelta(seconds=60))
    assert lease is not None
    loaded = await app.journal.load(RUN)
    assert loaded is not None
    started = value(
        await app.journal.checkpoint(
            lease,
            value(loaded.snapshot.run.start_gathering()),
            TraceEvent(1, "noise", "transition", FixedClock().now()),
            expected_revision=loaded.snapshot.revision,
        )
    )
    running = _Execution(agent_for(app, OverridingDecisions()), lease, started)
    await running.execute()
    assert running.run.state == RunState.FAILED
    assert running.run.failure_reason == "Investigation context is missing."


def is_tool_level_injection(request: DecisionRequest) -> bool:
    return request.purpose == "injection" and request.key in TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "flagged"), [(0.5, True), (0.51, True), (0.49, False)])
async def test_a_tool_result_is_inspected_per_record_from_an_injection_verdict_of_one_half(
    verdict: float, flagged: bool
) -> None:
    decisions = changing(is_tool_level_injection, verdict)
    await execute(await create_run(), decisions)
    inspected = {
        r.key for r in decisions.requests if r.purpose == "injection" and r.key not in TOOLS
    }
    assert ("note-002" in inspected) is flagged


@pytest.mark.asyncio
@pytest.mark.parametrize(("verdict", "flagged"), [(0.5, True), (0.51, True), (0.49, False)])
async def test_a_record_is_flagged_as_instruction_like_from_a_verdict_of_one_half(
    verdict: float, flagged: bool
) -> None:
    def matches(request: DecisionRequest) -> bool:
        return request.purpose == "injection"

    def answer(request: DecisionRequest, response: DecisionResponse) -> DecisionResponse:
        if not matches(request):
            return response
        return replace(response, value=1.0 if request.key in TOOLS else verdict)

    model = FakeLanguageModel()
    await execute(await create_run(), OverridingDecisions(answer), model=model)
    flagged_keys = [s.key for s in model.requests[0].signals if s.key.startswith("instruction:")]
    assert (flagged_keys == ["instruction:note-002"]) is flagged
    assert bool(flagged_keys) is flagged


@pytest.mark.asyncio
async def test_a_note_is_inspected_by_its_own_text() -> None:
    decisions = changing(is_tool_level_injection, 1.0)
    await execute(await create_run(), decisions)
    note = next(n for n in load_seed().notes if n.record_id == "note-002")
    (request,) = [r for r in decisions.requests if r.key == "note-002"]
    assert request.state_json == state_json({"text": note.text})


@pytest.mark.asyncio
async def test_a_document_is_inspected_by_its_text_followed_by_its_field_values() -> None:
    app = await create_run(subject="demo-customer-101", kind=AlertKind.PAYMENT_FRAUD, tools=6)
    decisions = changing(is_tool_level_injection, 1.0)
    await execute(app, decisions, tools=banking_tools())
    documents = [
        d.to_record()
        for d in load_seed(banking=True).documents
        if d.customer_id == "demo-customer-101"
    ]
    assert documents
    for document in documents:
        (request,) = [r for r in decisions.requests if r.key == document.record_id]
        expected = document.text + " " + " ".join(field.value for field in document.fields)
        assert request.state_json == state_json({"text": expected})


@pytest.mark.asyncio
async def test_each_signal_is_scored_by_its_kind() -> None:
    decisions = OverridingDecisions()
    await execute(await create_run(), decisions)
    scored = [r for r in decisions.requests if r.purpose == "signal"]
    assert {r.key.split(":")[0] for r in scored} >= {"volume", "instruction"}
    for request in scored:
        kind = state(request)["signal_kind"]
        assert kind == request.key.split(":")[0]
        assert kind != request.key.split(":")[-1]


class ForeignCurrency(TypedEvidenceGateway):
    def __init__(self) -> None:
        super().__init__(MemoryInvestigationTools(load_seed()))

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        result = await super().fetch(tool, query)
        if not isinstance(result, Ok):
            return result
        batch = result.value
        return Ok(
            replace(
                batch,
                records=tuple(
                    replace(r, currency="USD") if isinstance(r, Transaction) else r
                    for r in batch.records
                ),
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "scored"), [(LEGACY_FINDINGS, {"txn-002"}), ("findings-v2", set())]
)
async def test_tabular_risk_covers_foreign_currency_transfers_only_under_the_legacy_policy(
    policy: str, scored: set[str]
) -> None:
    decisions = OverridingDecisions()
    await execute(
        await create_run(),
        decisions,
        tools=ForeignCurrency(),
        risk_enabled=True,
        findings_policy=policy,
    )
    assert {r.key for r in decisions.requests if r.purpose == "risk"} == scored


def final_request(decisions: OverridingDecisions) -> DecisionRequest:
    (request,) = [r for r in decisions.requests if r.purpose == "recommendation"]
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize(("estimate", "counted"), [(0.5, 1), (0.51, 1), (0.49, 0)])
async def test_a_risk_estimate_counts_as_a_concern_from_one_half(
    estimate: float, counted: int
) -> None:
    baseline = changing(lambda r: r.purpose == "risk", 0.0)
    await execute(await create_run(), baseline, risk_enabled=True)
    decisions = changing(lambda r: r.purpose == "risk", estimate)
    await execute(await create_run(), decisions, risk_enabled=True)
    delta = (
        state(final_request(decisions))["signal_count"]
        - state(final_request(baseline))["signal_count"]
    )
    assert delta == counted


@pytest.mark.asyncio
async def test_the_recommendation_request_names_the_workflow_and_evidence_gaps() -> None:
    decisions = OverridingDecisions()
    await execute(await create_run(), decisions)
    asked = state(final_request(decisions))
    alert_type = next(r for r in decisions.requests if r.purpose == "alert_type")
    assert alert_type.options == ("unusual_transaction", "sanctions_match", "kyc_inconsistency")
    assert asked["workflow"] in alert_type.options
    assert asked["evidence_gaps"] is False


@pytest.mark.asyncio
async def test_the_legacy_policy_sends_the_recommendation_decision_no_workflow_or_gaps() -> None:
    decisions = OverridingDecisions()
    await execute(await create_run(), decisions, findings_policy=LEGACY_FINDINGS)
    asked = state(final_request(decisions))
    assert "workflow" not in asked and "evidence_gaps" not in asked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject", "documents", "gaps"),
    [
        pytest.param("demo-customer-101", True, False, id="complete-dossier"),
        pytest.param("demo-customer-101", False, True, id="documents-missing"),
        pytest.param("demo-customer-104", False, True, id="nothing-returned-at-all"),
    ],
)
async def test_a_banking_recommendation_request_names_its_workflow_and_whether_evidence_is_missing(
    subject: str, documents: bool, gaps: bool
) -> None:
    app = await create_run(subject=subject, kind=AlertKind.PAYMENT_FRAUD, tools=6)
    decisions = OverridingDecisions()
    await execute(app, decisions, tools=banking_tools(without_documents=not documents))
    asked = state(final_request(decisions))
    assert asked["workflow"] == "payment_fraud"
    assert asked["evidence_gaps"] is gaps


@pytest.mark.asyncio
async def test_a_banking_workflow_that_returned_nothing_cannot_close_and_says_why() -> None:
    app = await create_run(subject="demo-customer-104", kind=AlertKind.PAYMENT_FRAUD, tools=6)
    decisions = changing(lambda r: r.purpose == "recommendation", "close")
    run, events = await execute(app, decisions, tools=banking_tools())
    assert run.analysis is not None
    assert run.analysis.recommendation == Recommendation.REQUEST_INFORMATION
    (constraint,) = [e for e in events if e.operation == "policy.recommendation.v1"]
    assert constraint.output_json == state_json(
        {"recommendation": "request_information", "reasons": ["missing_kyc", "evidence_gaps"]}
    )


class RecordingModel(FakeLanguageModel):
    def __init__(self, tokens: Callable[[ModelRequest], int] | None = None) -> None:
        super().__init__()
        self.tokens = tokens

    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await super().generate(request)
        if self.tokens is None:
            return response
        return replace(response, output_tokens=self.tokens(request))


@pytest.mark.asyncio
async def test_alternative_models_are_offered_in_name_order_after_the_primary() -> None:
    decisions = OverridingDecisions()
    await execute(
        await create_run(),
        decisions,
        alternative_models={
            "mid": RecordingModel(),
            "alpha": RecordingModel(),
            "zeta": RecordingModel(),
        },
    )
    (route,) = [r for r in decisions.requests if r.purpose == "model_route"]
    assert route.options == ("primary", "alpha", "mid", "zeta")


@pytest.mark.asyncio
@pytest.mark.parametrize(("choice", "cap"), [("primary", 1024), ("alpha", 512)])
async def test_the_routed_model_writes_the_narrative_within_its_own_cap(
    choice: str, cap: int
) -> None:
    primary, alternative = RecordingModel(), RecordingModel()
    decisions = changing(lambda r: r.purpose == "model_route", choice)
    run, _ = await execute(
        await create_run(),
        decisions,
        model=primary,
        alternative_models={"alpha": alternative},
    )
    used, unused = (primary, alternative) if choice == "primary" else (alternative, primary)
    assert run.state == RunState.AWAITING_REVIEW
    assert [r.max_output_tokens for r in used.requests] == [cap]
    assert unused.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("extra", "accepted"), [(0, True), (1, False)])
async def test_a_narrative_may_use_the_whole_output_cap_but_not_more(
    extra: int, accepted: bool
) -> None:
    model = RecordingModel(lambda request: request.max_output_tokens + extra)
    run, _ = await execute(await create_run(), OverridingDecisions(), model=model)
    assert (run.state == RunState.AWAITING_REVIEW) is accepted
    if not accepted:
        assert run.failure_reason == "Model exceeded the authorized output token limit."
