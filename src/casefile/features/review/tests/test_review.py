from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result

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
) -> Result[Review, object]:
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
    assert isinstance(
        ConclusionReview.create(
            FIRST, "Original", action, corrected_statement=correction, reason=reason
        ),
        Err,
    )


def test_blank_original_is_invalid_and_rejection_is_retained() -> None:
    assert isinstance(ConclusionReview.create(FIRST, " ", ReviewAction.VALIDATE), Err)
    rejected = value(
        ConclusionReview.create(
            FIRST, "Original", ReviewAction.REJECT, reason="Source insuffisante"
        )
    )
    review = value(submit((rejected,)))
    assert review.conclusions == (rejected,)
    assert review.corrections == ()


def test_review_requires_explicit_confirmation_and_timezone() -> None:
    assert isinstance(submit((item(),), confirmed=False), Err)
    assert isinstance(submit((item(),), at=datetime(2026, 10, 24)), Err)


@pytest.mark.parametrize("reviews", [(), (item(), item()), (item(SECOND),), (item(), item(SECOND))])
def test_review_covers_exactly_the_expected_conclusions(
    reviews: tuple[ConclusionReview, ...],
) -> None:
    assert isinstance(submit(reviews), Err)


def test_duplicate_expected_ids_are_rejected() -> None:
    assert isinstance(submit((item(),), (FIRST, FIRST)), Err)


@given(expected=st.sets(st.integers(1, 10)), reviewed=st.lists(st.integers(1, 10), max_size=15))
def test_review_completeness_is_set_equality_without_duplicates(
    expected: set[int], reviewed: list[int]
) -> None:
    expected_ids = tuple(ConclusionId(UUID(int=number)) for number in sorted(expected))
    decisions = tuple(item(ConclusionId(UUID(int=number))) for number in reviewed)
    result = submit(decisions, expected_ids)
    assert isinstance(result, Ok) == (
        expected == set(reviewed) and len(reviewed) == len(set(reviewed))
    )


def test_empty_analysis_still_requires_a_human_review() -> None:
    assert isinstance(submit((), (), confirmed=False), Err)
    assert value(submit((), ())).conclusions == ()


def test_review_is_immutable_and_cannot_be_created_unchecked() -> None:
    review = value(submit((item(),)))
    for target, field in ((review, "decision"), (review.conclusions[0], "action")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, "Changed")
    with pytest.raises(TypeError, match="factory"):
        Review()
