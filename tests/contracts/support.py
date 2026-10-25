from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from casefile.features.case_intake.application.ports import CaseRepository
from casefile.features.case_intake.domain.case import AlertKind, Case
from casefile.features.evaluation.application.ports import CorrectionLookup, ReferenceRepository
from casefile.features.investigation.application.agent_types import ExecutionJournal
from casefile.features.investigation.application.jobs import JobQueue
from casefile.features.investigation.application.ports import CaseLookup, RunStore
from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.features.investigation.domain.run import Run
from casefile.features.review.application.ports import ReviewGateway, ReviewReader
from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.shared.ids import CaseId, ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Ok, Result

AT = datetime(2026, 10, 24, 12, tzinfo=UTC)
CASE = CaseId(UUID(int=1))
RUN = RunId(UUID(int=2))
CONCLUSION = ConclusionId(UUID(int=3))
REVIEW = ReviewId(UUID(int=4))
REVIEWER = ReviewerId(UUID(int=5))


class FixedClock:
    def now(self) -> datetime:
        return AT


def value[T, E](result: Result[T, E]) -> T:
    assert isinstance(result, Ok), result
    return result.value


@dataclass
class Backend:
    cases: CaseRepository
    runs: RunStore
    case_lookup: CaseLookup
    gateway: ReviewGateway
    reviews: ReviewReader
    corrections: CorrectionLookup
    references: ReferenceRepository
    queue: JobQueue
    engine: AsyncEngine | None = None
    journal: ExecutionJournal | None = None


def case() -> Case:
    return value(
        Case.create(
            CASE, "alert-001", "customer-001", AlertKind.SANCTIONS_MATCH, "Évaluation fictive", AT
        )
    )


def pending() -> Run:
    return Run.new(RUN, CASE, value(BudgetLimits.create(5, 100, Decimal("1.00000000000000000001"))))


def awaiting() -> Run:
    source = value(SourceReference.create("sanctions", "record/é-1"))
    run = value(pending().start_gathering())
    run = value(run.record_sources((source,)))
    run = value(run.consume(value(BudgetUsage.create(1, 20, Decimal("0.00000000000000000001")))))
    run = value(run.start_analysis())
    item = value(
        Conclusion.create(
            CONCLUSION, "Homonymie probable", value(Confidence.create(0.75)), (source,)
        )
    )
    return value(
        run.submit_analysis(
            value(Analysis.create("Analyse fictive", (item,), Recommendation.ESCALATE))
        )
    )


def review(identifier: ReviewId = REVIEW) -> Review:
    item = value(
        ConclusionReview.create(
            CONCLUSION,
            "Homonymie probable",
            ReviewAction.CORRECT,
            corrected_statement="Homonymie confirmée",
            reason="Profil distinct",
        )
    )
    return value(
        Review.submit(
            identifier,
            RUN,
            REVIEWER,
            (CONCLUSION,),
            (item,),
            FinalDecision.CLOSE,
            AT,
            confirmed=True,
        )
    )


async def seed(backend: Backend, *, ready_for_review: bool = False) -> None:
    value(await backend.cases.add(case()))
    value(await backend.runs.enqueue(pending()))
    if ready_for_review:
        value(await backend.runs.save(awaiting(), expected_revision=1))
