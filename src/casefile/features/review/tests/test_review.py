from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.features.review.tests.support import error_code
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Ok, Result

FIRST = ConclusionId(UUID(int=1))
SECOND = ConclusionId(UUID(int=2))
AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


def value[T, E](result: Result[T, E]) -> T:
    assert isinstance(result, Ok), result
    return result.value


def item(identifier: ConclusionId = FIRST) -> ConclusionReview:
    return value(ConclusionReview.create(identifier, "Montant inhabituel", ReviewAction.VALIDATE))


def submit(
    conclusions: tuple[ConclusionReview, ...],
    expected: tuple[ConclusionId, ...] = (FIRST,),
    *,
    confirmed: bool = True,
    at: datetime = AT,
) -> Result[Review, ReviewError]:
    return Review.submit(
        ReviewId(UUID(int=10)),
        RunId(UUID(int=11)),
        ReviewerId(UUID(int=12)),
        expected,
        conclusions,
        FinalDecision.ESCALATE,
        at,
        confirmed=confirmed,
    )


def test_correction_retains_before_after_and_reason() -> None:
    correction = value(
        ConclusionReview.create(
            FIRST,
            "Ancienne conclusion",
            ReviewAction.CORRECT,
            corrected_statement="Conclusion corrigée",
            reason="Relecture de la source",
        )
    )
    review = value(submit((correction,)))
    assert review.corrections == (correction,)
    assert correction.original_statement == "Ancienne conclusion"
    assert correction.corrected_statement == "Conclusion corrigée"
    assert correction.reason == "Relecture de la source"
    assert review.reviewer_id == ReviewerId(UUID(int=12))
    assert review.submitted_at == AT


@pytest.mark.parametrize(
    ("action", "correction", "reason"),
    [
        (ReviewAction.CORRECT, None, "Reason"),
        (ReviewAction.CORRECT, " ", "Reason"),
        (ReviewAction.CORRECT, "New", None),
        (ReviewAction.CORRECT, "New", " "),
        (ReviewAction.REJECT, None, None),
        (ReviewAction.REJECT, "New", "Reason"),
        (ReviewAction.VALIDATE, "New", None),
        (ReviewAction.VALIDATE, None, " "),
    ],
)
def test_invalid_review_actions_return_business_errors(
    action: ReviewAction, correction: str | None, reason: str | None
) -> None:
    result = ConclusionReview.create(
        FIRST, "Original", action, corrected_statement=correction, reason=reason
    )
    assert error_code(result) == ReviewErrorCode.INVALID_CONCLUSION_REVIEW


@pytest.mark.parametrize("action", list(ReviewAction))
def test_blank_original_is_invalid_for_every_action(action: ReviewAction) -> None:
    result = ConclusionReview.create(
        FIRST,
        " ",
        action,
        corrected_statement="New" if action == ReviewAction.CORRECT else None,
        reason="Reason" if action != ReviewAction.VALIDATE else None,
    )
    assert error_code(result) == ReviewErrorCode.INVALID_CONCLUSION_REVIEW


@pytest.mark.parametrize(
    ("action", "correction", "reason"),
    [
        (ReviewAction.VALIDATE, None, None),
        (ReviewAction.VALIDATE, None, "Source relue"),
        (ReviewAction.CORRECT, "New", "Reason"),
        (ReviewAction.REJECT, None, "Reason"),
    ],
)
def test_valid_review_actions_retain_exactly_what_was_given(
    action: ReviewAction, correction: str | None, reason: str | None
) -> None:
    review = value(
        ConclusionReview.create(
            FIRST, "Original", action, corrected_statement=correction, reason=reason
        )
    )
    assert (review.conclusion_id, review.original_statement, review.action) == (
        FIRST,
        "Original",
        action,
    )
    assert (review.corrected_statement, review.reason) == (correction, reason)


def test_rejection_is_retained() -> None:
    rejected = value(
        ConclusionReview.create(
            FIRST, "Original", ReviewAction.REJECT, reason="Source insuffisante"
        )
    )
    review = value(submit((rejected,)))
    assert review.conclusions == (rejected,)
    assert review.corrections == ()


def test_review_requires_explicit_confirmation_and_timezone() -> None:
    assert error_code(submit((item(),), confirmed=False)) == ReviewErrorCode.CONFIRMATION_REQUIRED
    naive = datetime(2026, 10, 24)
    assert error_code(submit((item(),), at=naive)) == ReviewErrorCode.INVALID_TIMESTAMP


def test_confirmation_is_checked_before_the_timestamp_and_the_coverage() -> None:
    result = submit((), (FIRST,), confirmed=False, at=datetime(2026, 10, 24))
    assert error_code(result) == ReviewErrorCode.CONFIRMATION_REQUIRED
    result = submit((), (FIRST,), at=datetime(2026, 10, 24))
    assert error_code(result) == ReviewErrorCode.INVALID_TIMESTAMP


@pytest.mark.usefixtures("local_timezone")
def test_submission_time_is_normalized_to_utc_whatever_the_process_timezone() -> None:
    paris = timezone(timedelta(hours=2))
    review = value(submit((item(),), at=datetime(2026, 10, 24, 14, tzinfo=paris)))
    assert review.submitted_at == AT
    assert review.submitted_at.utcoffset() == timedelta(0)
    assert review.submitted_at.tzname() == "UTC"


@pytest.mark.parametrize("reviews", [(), (item(), item()), (item(SECOND),), (item(), item(SECOND))])
def test_review_covers_exactly_the_expected_conclusions(
    reviews: tuple[ConclusionReview, ...],
) -> None:
    assert error_code(submit(reviews)) == ReviewErrorCode.INCOMPLETE_REVIEW


@pytest.mark.parametrize("reviews", [(item(),), (item(), item())])
def test_duplicate_expected_ids_are_rejected(reviews: tuple[ConclusionReview, ...]) -> None:
    assert error_code(submit(reviews, (FIRST, FIRST))) == ReviewErrorCode.INCOMPLETE_REVIEW


@given(expected=st.sets(st.integers(1, 10)), reviewed=st.lists(st.integers(1, 10), max_size=15))
def test_review_completeness_is_set_equality_without_duplicates(
    expected: set[int], reviewed: list[int]
) -> None:
    expected_ids = tuple(ConclusionId(UUID(int=number)) for number in sorted(expected))
    decisions = tuple(item(ConclusionId(UUID(int=number))) for number in reviewed)
    result = submit(decisions, expected_ids)
    complete = expected == set(reviewed) and len(reviewed) == len(set(reviewed))
    assert isinstance(result, Ok) == complete
    if not complete:
        assert error_code(result) == ReviewErrorCode.INCOMPLETE_REVIEW


def test_empty_analysis_still_requires_a_human_review() -> None:
    assert error_code(submit((), (), confirmed=False)) == ReviewErrorCode.CONFIRMATION_REQUIRED
    assert value(submit((), ())).conclusions == ()


def test_review_is_immutable_and_cannot_be_created_unchecked() -> None:
    review = value(submit((item(),)))
    for target, field in ((review, "decision"), (review.conclusions[0], "action")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, "Changed")
    with pytest.raises(TypeError, match="factory"):
        Review()
