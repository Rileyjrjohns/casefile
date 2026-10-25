from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from casefile.bootstrap.memory import MemoryApplication, build_memory_application
from casefile.features.case_intake.application.open_case import OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.evaluation.application.promote_correction import PromoteCorrectionCommand
from casefile.features.evaluation.domain.errors import EvaluationErrorCode
from casefile.features.investigation.application.start_run import StartRunCommand
from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.run import RunState
from casefile.features.review.application.record_review import RecordReviewCommand, ReviewInput
from casefile.features.review.domain.errors import ReviewErrorCode
from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.shared.ids import CaseId, ConclusionId, ReferenceId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result

CASE = CaseId(UUID(int=1))
RUN = RunId(UUID(int=2))
CONCLUSION = ConclusionId(UUID(int=3))
REVIEWER = ReviewerId(UUID(int=4))
REVIEW = ReviewId(UUID(int=5))
AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


def value[T, E](result: Result[T, E]) -> T:
    assert isinstance(result, Ok), result
    return result.value


async def prepare() -> MemoryApplication:
    app = build_memory_application(FixedClock())
    value(
        await app.open_case.execute(
            OpenCaseCommand(
                CASE, "alert-1", "subject-1", AlertKind.SANCTIONS_MATCH, "Correspondance à vérifier"
            )
        )
    )
    run = value(
        await app.start_run.execute(
            StartRunCommand(RUN, CASE, value(BudgetLimits.create(5, 100, Decimal(1))))
        )
    )
    assert isinstance(await app.review_gateway.load(RUN), Err)
    source = value(SourceReference.create("sanctions", "record-1"))
    run = value(run.start_gathering())
    run = value(run.record_sources((source,)))
    run = value(run.start_analysis())
    conclusion = value(
        Conclusion.create(
            CONCLUSION, "Homonymie probable", value(Confidence.create(0.7)), (source,)
        )
    )
    run = value(
        run.submit_analysis(
            value(Analysis.create("Vérification requise", (conclusion,), Recommendation.ESCALATE))
        )
    )
    value(await app.runs.save(run, expected_revision=1))
    app.reviewer_access.allowed.add((REVIEWER, RUN))
    app.promotion_access.allowed.add(REVIEWER)
    return app


def command(action: ReviewAction = ReviewAction.CORRECT) -> RecordReviewCommand:
    item = ReviewInput(
        CONCLUSION,
        action,
        "Homonymie confirmée" if action == ReviewAction.CORRECT else None,
        "Profil distinct" if action != ReviewAction.VALIDATE else None,
    )
    return RecordReviewCommand(REVIEW, RUN, REVIEWER, 2, (item,), FinalDecision.CLOSE, True)


@pytest.mark.asyncio
async def test_open_start_review_and_promote_are_wired_without_feature_imports() -> None:
    app = await prepare()
    reviewed = value(await app.record_review.execute(command()))
    snapshot = await app.runs.get(RUN)
    assert snapshot is not None and snapshot.run.state == RunState.CLOSED
    assert snapshot.run.human_decision is not None
    assert snapshot.run.human_decision.review_id == reviewed.id
    assert snapshot.run.human_decision.recommendation == Recommendation.CLOSE
    assert app.reviews.reviews[reviewed.id] == reviewed
    promoted = value(
        await app.promote_correction.execute(
            PromoteCorrectionCommand(ReferenceId(UUID(int=6)), REVIEW, CONCLUSION, REVIEWER)
        )
    )
    assert promoted.correction.case_id == CASE
    assert promoted.correction.original_statement == "Homonymie probable"
    assert promoted.correction.corrected_statement == "Homonymie confirmée"
    assert snapshot.run.analysis is not None
    assert snapshot.run.analysis.conclusions[0].statement == "Homonymie probable"
    assert app.runs.jobs == [RUN]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage", ["runs", "reviews"])
async def test_atomic_review_commit_leaves_both_aggregates_unchanged_on_failure(
    storage: str,
) -> None:
    app = await prepare()
    before = await app.runs.get(RUN)
    if storage == "runs":
        app.runs.fail_writes = True
    else:
        app.reviews.fail_writes = True
    with pytest.raises(RuntimeError, match="storage"):
        await app.record_review.execute(command())
    assert await app.runs.get(RUN) == before
    assert app.reviews.reviews == {}


@pytest.mark.asyncio
async def test_two_reviews_of_same_snapshot_cannot_both_commit() -> None:
    app = await prepare()
    first = value(await app.record_review.execute(command()))
    second = value(
        Review.submit(
            ReviewId(UUID(int=99)),
            RUN,
            REVIEWER,
            (CONCLUSION,),
            first.conclusions,
            FinalDecision.ESCALATE,
            AT,
            confirmed=True,
        )
    )
    result = await app.review_gateway.commit(second, expected_revision=2)
    assert isinstance(result, Err) and result.error.code == ReviewErrorCode.CONFLICT
    assert list(app.reviews.reviews.values()) == [first]
    assert isinstance(await app.review_gateway.load(RUN), Err)


@pytest.mark.asyncio
async def test_commit_rejects_forged_originals_and_duplicate_review_id() -> None:
    app = await prepare()
    forged_item = value(ConclusionReview.create(CONCLUSION, "Inventé", ReviewAction.VALIDATE))
    forged = value(
        Review.submit(
            REVIEW,
            RUN,
            REVIEWER,
            (CONCLUSION,),
            (forged_item,),
            FinalDecision.CLOSE,
            AT,
            confirmed=True,
        )
    )
    assert isinstance(await app.review_gateway.commit(forged, expected_revision=2), Err)
    assert app.reviews.reviews == {}
    app.reviews.reviews[REVIEW] = forged
    result = await app.record_review.execute(command())
    assert isinstance(result, Err) and result.error.code == ReviewErrorCode.REVIEW_ID_CONFLICT
    snapshot = await app.runs.get(RUN)
    assert snapshot is not None and snapshot.run.state == RunState.AWAITING_REVIEW


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [ReviewAction.VALIDATE, ReviewAction.REJECT])
async def test_only_persisted_corrections_can_be_promoted(action: ReviewAction) -> None:
    app = await prepare()
    assert isinstance(await app.correction_lookup.get(REVIEW, CONCLUSION), Err)
    value(await app.record_review.execute(command(action)))
    result = await app.promote_correction.execute(
        PromoteCorrectionCommand(ReferenceId(UUID(int=6)), REVIEW, CONCLUSION, REVIEWER)
    )
    assert isinstance(result, Err) and result.error.code == EvaluationErrorCode.NOT_A_CORRECTION
    assert app.references.examples == {}


@pytest.mark.asyncio
async def test_missing_runs_and_conclusions_are_not_exposed_as_corrections() -> None:
    app = await prepare()
    assert isinstance(await app.review_gateway.load(RunId(UUID(int=99))), Err)
    value(await app.record_review.execute(command()))
    assert isinstance(await app.correction_lookup.get(REVIEW, ConclusionId(UUID(int=99))), Err)
    app.runs.runs.clear()
    assert isinstance(await app.correction_lookup.get(REVIEW, CONCLUSION), Err)
