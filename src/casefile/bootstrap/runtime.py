"""Compose the shared API/MCP services and the asynchronous worker."""

from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from pydantic import JsonValue, TypeAdapter

from casefile.bootstrap.agent import build_agent_worker
from casefile.bootstrap.demo_agent import SystemClock
from casefile.bootstrap.memory import MemoryApplication, build_memory_application
from casefile.bootstrap.postgres import PostgresApplication
from casefile.features.case_intake.adapters.inbound.documents import OpenCaseDocument
from casefile.features.case_intake.application.queries import CaseQueries
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.case_intake.domain.errors import IntakeError
from casefile.features.evaluation.adapters.inbound.documents import PromoteReferenceDocument
from casefile.features.evaluation.application.promote_correction import PromoteCorrectionCommand
from casefile.features.evaluation.domain.errors import EvaluationError
from casefile.features.investigation.adapters.inbound.documents import StartRunDocument
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.journal_sql import TraceDocument
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.documents import encode_run
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.banking_policy import BANKING_KINDS
from casefile.features.investigation.application.start_run import StartRunCommand
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.errors import InvestigationError
from casefile.features.review.adapters.inbound.documents import ReviewDocument
from casefile.features.review.application.record_review import RecordReview
from casefile.features.review.domain.errors import ReviewError
from casefile.shared.ids import CaseId, ConclusionId, ReferenceId, ReviewerId, RunId
from casefile.shared.result import Err, Result

DEMO_REVIEWER = ReviewerId(UUID("00000000-0000-0000-0000-000000000101"))


@dataclass(frozen=True, slots=True)
class DemoReviewerAccess:
    async def can_review(self, reviewer_id: ReviewerId, run_id: RunId) -> bool:
        return reviewer_id == DEMO_REVIEWER


@dataclass(frozen=True, slots=True)
class DemoPromotionAccess:
    async def can_promote(self, actor_id: ReviewerId) -> bool:
        return actor_id == DEMO_REVIEWER


def correction_reference_id(review_id: UUID, conclusion_id: UUID) -> ReferenceId:
    return ReferenceId(uuid5(review_id, f"correction:{conclusion_id}"))


class TransportFailure(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def unwrap[T](
    result: Result[T, IntakeError | InvestigationError | ReviewError | EvaluationError],
) -> T:
    if isinstance(result, Err):
        code = result.error.code.value
        status = (
            404
            if code.endswith("not_found")
            else 403
            if code == "access_denied"
            else 409
            if code
            in {
                "conflict",
                "active_run_exists",
                "run_already_exists",
                "case_already_exists",
                "alert_already_exists",
                "run_not_reviewable",
                "review_id_conflict",
                "reference_id_conflict",
            }
            else 422
        )
        raise TransportFailure(code, result.error.message, status)
    return result.value


def document[T](value: T) -> dict[str, JsonValue]:
    return TypeAdapter(dict[str, JsonValue]).validate_json(
        TypeAdapter(type(value)).dump_json(value)
    )


@dataclass(frozen=True, slots=True)
class Runtime:
    app: MemoryApplication | PostgresApplication
    worker: RunWorker
    reviewer: ReviewerId = DEMO_REVIEWER

    async def list_cases(self, limit: int = 50, offset: int = 0) -> dict[str, JsonValue]:
        cases = await CaseQueries(self.app.cases).list(limit=limit, offset=offset)
        return {"cases": [document(case) for case in cases], "limit": limit, "offset": offset}

    async def get_case(self, case_id: UUID) -> dict[str, JsonValue]:
        case = await CaseQueries(self.app.cases).get(CaseId(case_id))
        if case is None:
            raise TransportFailure("case_not_found", "Case does not exist.", 404)
        return document(case)

    async def open_case(self, payload: OpenCaseDocument) -> dict[str, JsonValue]:
        return document(unwrap(await self.app.open_case.execute(payload.command())))

    async def start_run(self, case_id: UUID, payload: StartRunDocument) -> dict[str, JsonValue]:
        calls = payload.tool_calls
        case = await self.app.cases.get(CaseId(case_id))
        if (
            case is not None
            and case.kind in BANKING_KINDS
            and "tool_calls" not in payload.model_fields_set
        ):
            calls = 6
        limits = unwrap(BudgetLimits.create(calls, payload.tokens, payload.estimated_cost))
        run = unwrap(
            await self.app.start_run.execute(
                StartRunCommand(RunId(payload.id), CaseId(case_id), limits)
            )
        )
        return document(encode_run(run))

    async def case_runs(self, case_id: UUID) -> dict[str, JsonValue]:
        await self.get_case(case_id)
        snapshots = await self.app.runs.for_case(CaseId(case_id))
        return {"runs": [document(encode_run(item.run)) for item in snapshots]}

    async def get_run(self, run_id: UUID) -> dict[str, JsonValue]:
        snapshot = await self.app.journal.load(RunId(run_id))
        if snapshot is None:
            raise TransportFailure("run_not_found", "Run does not exist.", 404)
        receipt = snapshot.snapshot.run.human_decision
        review = await self.app.reviews.get(receipt.review_id) if receipt else None
        references: dict[str, JsonValue] = {}
        if review is not None:
            for item in review.conclusions:
                reference = await self.app.references.get(
                    correction_reference_id(review.id, item.conclusion_id)
                )
                if reference is not None:
                    references[str(item.conclusion_id)] = str(reference.id)
        return {
            "run": document(encode_run(snapshot.snapshot.run)),
            "revision": snapshot.snapshot.revision,
            "review": document(review) if review is not None else None,
            "events": [document(TraceDocument.model_validate(event)) for event in snapshot.events],
            **({"references": references} if references else {}),
        }

    async def promote_reference(
        self, run_id: UUID, conclusion_id: UUID, payload: PromoteReferenceDocument
    ) -> dict[str, JsonValue]:
        if not await self.app.promote_correction.access.can_promote(self.reviewer):
            raise TransportFailure("access_denied", "Actor cannot curate references.", 403)
        if not payload.confirmed:
            raise TransportFailure(
                "confirmation_required", "Confirm saving this reviewed correction."
            )
        snapshot = await self.app.runs.get(RunId(run_id))
        if snapshot is None:
            raise TransportFailure("run_not_found", "Run does not exist.", 404)
        receipt = snapshot.run.human_decision
        if receipt is None:
            raise TransportFailure(
                "review_required", "A committed human correction is required.", 409
            )
        reference = unwrap(
            await self.app.promote_correction.execute(
                PromoteCorrectionCommand(
                    correction_reference_id(receipt.review_id, conclusion_id),
                    receipt.review_id,
                    ConclusionId(conclusion_id),
                    self.reviewer,
                )
            )
        )
        return document(reference)

    async def reference_bundle(self, reference_id: UUID) -> dict[str, JsonValue]:
        if not await self.app.promote_correction.access.can_promote(self.reviewer):
            raise TransportFailure("access_denied", "Actor cannot read curated references.", 403)
        reference = await self.app.references.get(ReferenceId(reference_id))
        if reference is None:
            raise TransportFailure("reference_not_found", "Reference does not exist.", 404)
        snapshot = await self.app.journal.load(reference.correction.run_id)
        review = await self.app.reviews.get(reference.correction.review_id)
        if snapshot is None or snapshot.snapshot.run.analysis is None or review is None:
            raise RuntimeError("A stored reference requires its reviewed investigation.")
        original = {c.id: c for c in snapshot.snapshot.run.analysis.conclusions}
        expected: list[JsonValue] = []
        for item in review.conclusions:
            if item.action.value == "reject":
                continue
            conclusion = original[item.conclusion_id]
            expected.append(
                {
                    "conclusion_id": str(item.conclusion_id),
                    "statement": item.corrected_statement or item.original_statement,
                    "sources": [document(source) for source in conclusion.sources],
                }
            )
        return {
            "schema_version": 1,
            "status": "requires_curation",
            "reference": document(reference),
            "case": await self.get_case(reference.correction.case_id),
            "investigation": await self.get_run(reference.correction.run_id),
            "expected": {"recommendation": review.decision.value, "conclusions": expected},
        }

    async def review(self, run_id: UUID, payload: ReviewDocument) -> dict[str, JsonValue]:
        return document(
            unwrap(
                await self.app.record_review.execute(payload.command(RunId(run_id), self.reviewer))
            )
        )

    async def source(self, run_id: UUID, tool: str, record_id: str) -> dict[str, JsonValue]:
        snapshot = await self.app.journal.load(RunId(run_id))
        if snapshot is not None:
            for event in snapshot.events:
                if (
                    event.operation == f"decision.risk.{record_id}"
                    and event.phase == "succeeded"
                    and event.output_json
                ):
                    response = AgentJsonCodec().decision_response(event.output_json)
                    if response.model == tool:
                        return {
                            "tool": tool,
                            "record": {
                                "record_id": record_id,
                                "prediction": document(response),
                                "request": TypeAdapter(dict[str, JsonValue]).validate_json(
                                    event.input_json
                                ),
                            },
                        }
                if (
                    event.operation == f"tool.{tool}"
                    and event.phase == "succeeded"
                    and event.output_json
                ):
                    evidence = AgentJsonCodec().evidence(event.output_json)
                    for record in evidence.records:
                        if record.record_id == record_id:
                            return {"tool": tool, "record": document(record)}
        raise TransportFailure("source_not_found", "This record was not observed in this run.", 404)


async def seed_runtime(runtime: Runtime, *, banking: bool = False) -> None:
    for i in range(1, 4):
        case_id = UUID(int=1000 + i)
        if await runtime.app.cases.get(CaseId(case_id)) is not None:
            continue
        await runtime.open_case(
            OpenCaseDocument(
                id=case_id,
                alert_id=f"demo-alert-{i:03}",
                subject_id=f"demo-customer-{i:03}",
                kind=AlertKind.UNUSUAL_TRANSACTION,
                description=(
                    "Routine activity review",
                    "Transfer exceeds the declared volume",
                    "Sanctions and KYC investigation",
                )[i - 1],
            )
        )
        await runtime.start_run(case_id, StartRunDocument(id=UUID(int=2000 + i)))
    if banking:
        for number, kind, description in (
            (101, AlertKind.PAYMENT_FRAUD, "Supplier payment: disputed bank-account change"),
            (
                102,
                AlertKind.CREDIT_DOCUMENT_FRAUD,
                "Credit application: conflicting income evidence",
            ),
            (
                103,
                AlertKind.SANCTIONS_IDENTITY_REVIEW,
                "Sanctions alert: same name, different identity",
            ),
        ):
            case_id = UUID(int=1000 + number)
            if await runtime.app.cases.get(CaseId(case_id)) is None:
                await runtime.open_case(
                    OpenCaseDocument(
                        id=case_id,
                        alert_id=f"banking-alert-{number}",
                        subject_id=f"demo-customer-{number}",
                        kind=kind,
                        description=description,
                    )
                )


async def create_memory_runtime(*, seed: bool = True, banking: bool = False) -> Runtime:
    clock = SystemClock()
    app = build_memory_application(clock)
    app = replace(
        app,
        record_review=RecordReview(app.review_gateway, DemoReviewerAccess(), clock),
        promote_correction=replace(app.promote_correction, access=DemoPromotionAccess()),
    )
    runtime = Runtime(
        app,
        build_agent_worker(
            app,
            MemoryInvestigationTools(load_seed(banking=banking)),
            FakeLanguageModel(),
            clock,
            MemoryTelemetry(),
            risk_enabled=True,
        ),
    )
    if seed:
        await seed_runtime(runtime, banking=banking)
    return runtime
