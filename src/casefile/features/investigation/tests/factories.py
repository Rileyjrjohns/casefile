from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import HumanDecision, Run, RunState
from casefile.shared.ids import CaseId, ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result

RUN_ID = RunId(UUID(int=1))
CASE_ID = CaseId(UUID(int=2))
CONCLUSION_ID = ConclusionId(UUID(int=3))
REVIEW_ID = ReviewId(UUID(int=4))
REVIEWER_ID = ReviewerId(UUID(int=5))
AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


def value[T, E](result: Result[T, E]) -> T:
    assert isinstance(result, Ok), result
    return result.value


def error_code[T](result: Result[T, InvestigationError]) -> InvestigationErrorCode:
    """Return the business error code, failing the test when the result succeeded."""
    assert isinstance(result, Err), result
    return result.error.code


def source() -> SourceReference:
    return value(SourceReference.create("transactions", "tx-001"))


def conclusion() -> Conclusion:
    return value(
        Conclusion.create(
            CONCLUSION_ID, "Montant inhabituel", value(Confidence.create(0.8)), (source(),)
        )
    )


def analysis() -> Analysis:
    return value(
        Analysis.create("Une transaction inhabituelle.", (conclusion(),), Recommendation.ESCALATE)
    )


def decision() -> HumanDecision:
    return value(
        HumanDecision.create(
            RUN_ID,
            REVIEW_ID,
            REVIEWER_ID,
            (CONCLUSION_ID,),
            Recommendation.ESCALATE,
            AT,
            confirmed=True,
        )
    )


def run_at(state: RunState) -> Run:
    limits = value(BudgetLimits.create(5, 100, Decimal("1.00")))
    run = Run.new(RUN_ID, CASE_ID, limits)
    if state == RunState.PENDING:
        return run
    if state == RunState.FAILED:
        return value(run.fail("Tool unavailable"))
    run = value(run.start_gathering())
    run = value(run.record_sources((source(),)))
    if state == RunState.GATHERING:
        return run
    run = value(run.start_analysis())
    if state == RunState.ANALYZING:
        return run
    run = value(run.submit_analysis(analysis()))
    if state == RunState.AWAITING_REVIEW:
        return run
    return value(run.close(decision()))
