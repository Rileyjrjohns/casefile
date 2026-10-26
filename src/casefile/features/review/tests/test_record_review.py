from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from casefile.features.review.adapters.outbound.memory import (
    InMemoryReviewerAccess,
    InMemoryReviewGateway,
)
from casefile.features.review.application.ports import ReviewConclusion, ReviewTarget
from casefile.features.review.application.record_review import (
    RecordReview,
    RecordReviewCommand,
    ReviewInput,
)
from casefile.features.review.domain.errors import ReviewErrorCode
from casefile.features.review.domain.review import FinalDecision, ReviewAction
from casefile.features.review.tests.support import error_code
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Ok

RUN = RunId(UUID(int=1))
CONCLUSION = ConclusionId(UUID(int=2))
REVIEWER = ReviewerId(UUID(int=3))
AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


class FixedClock:
    def __init__(self, at: datetime = AT) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def gateway() -> InMemoryReviewGateway:
    result = InMemoryReviewGateway()
    result.targets[RUN] = ReviewTarget(
        RUN, 4, (ReviewConclusion(CONCLUSION, "Original autoritatif"),)
    )
    return result


def command(
    *, confirmed: bool = True, revision: int = 4, inputs: tuple[ReviewInput, ...] | None = None
) -> RecordReviewCommand:
    if inputs is None:
        inputs = (ReviewInput(CONCLUSION, ReviewAction.CORRECT, "Texte corrigé", "Source relue"),)
    return RecordReviewCommand(
        ReviewId(UUID(int=4)), RUN, REVIEWER, revision, inputs, FinalDecision.CLOSE, confirmed
    )


def use_case(
    store: InMemoryReviewGateway, *, authorized: bool = True, at: datetime = AT
) -> RecordReview:
    access = InMemoryReviewerAccess({(REVIEWER, RUN)} if authorized else set())
    return RecordReview(store, access, FixedClock(at))


@pytest.mark.asyncio
async def test_review_uses_persisted_originals_and_commits_once() -> None:
    store = gateway()
    result = await use_case(store).execute(command())
    assert isinstance(result, Ok)
    assert result.value.conclusions[0].original_statement == "Original autoritatif"
    assert result.value.submitted_at == AT
    assert store.reviews[result.value.id] == result.value
    assert store.closed_runs == {RUN}
    again = await use_case(store).execute(command())
    assert error_code(again) == ReviewErrorCode.RUN_NOT_REVIEWABLE
    assert len(store.reviews) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("unauthorized", ReviewErrorCode.ACCESS_DENIED),
        ("unconfirmed", ReviewErrorCode.CONFIRMATION_REQUIRED),
        ("stale", ReviewErrorCode.CONFLICT),
        ("missing", ReviewErrorCode.RUN_NOT_FOUND),
        ("empty", ReviewErrorCode.INCOMPLETE_REVIEW),
        ("duplicate", ReviewErrorCode.INCOMPLETE_REVIEW),
        ("unknown", ReviewErrorCode.UNKNOWN_CONCLUSION),
        ("bad_correction", ReviewErrorCode.INVALID_CONCLUSION_REVIEW),
    ],
)
async def test_invalid_review_has_no_commit(mode: str, code: ReviewErrorCode) -> None:
    store = gateway()
    request = command()
    if mode == "unconfirmed":
        request = command(confirmed=False)
    elif mode == "stale":
        request = command(revision=3)
    elif mode == "missing":
        store.targets.clear()
    elif mode == "empty":
        request = command(inputs=())
    elif mode == "duplicate":
        request = command(inputs=(ReviewInput(CONCLUSION, ReviewAction.VALIDATE),) * 2)
    elif mode == "unknown":
        request = command(inputs=(ReviewInput(ConclusionId(UUID(int=99)), ReviewAction.VALIDATE),))
    elif mode == "bad_correction":
        request = command(inputs=(ReviewInput(CONCLUSION, ReviewAction.CORRECT),))
    result = await use_case(store, authorized=mode != "unauthorized").execute(request)
    assert error_code(result) == code
    assert store.reviews == {} and store.closed_runs == set()


@pytest.mark.asyncio
async def test_authorization_is_checked_before_confirmation_and_the_run_lookup() -> None:
    store = gateway()
    store.targets.clear()
    result = await use_case(store, authorized=False).execute(command(confirmed=False))
    assert error_code(result) == ReviewErrorCode.ACCESS_DENIED
    result = await use_case(store).execute(command(confirmed=False))
    assert error_code(result) == ReviewErrorCode.CONFIRMATION_REQUIRED


@pytest.mark.asyncio
async def test_a_stale_revision_wins_over_conclusion_problems() -> None:
    store = gateway()
    unknown = (ReviewInput(ConclusionId(UUID(int=99)), ReviewAction.VALIDATE),)
    result = await use_case(store).execute(command(revision=3, inputs=unknown))
    assert error_code(result) == ReviewErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_review_is_stamped_with_the_clock_in_utc() -> None:
    store = gateway()
    local = datetime(2026, 10, 24, 14, tzinfo=timezone(timedelta(hours=2)))
    result = await use_case(store, at=local).execute(command())
    assert isinstance(result, Ok)
    assert result.value.submitted_at == AT
    assert result.value.submitted_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_review_carries_the_command_identity_and_decision() -> None:
    result = await use_case(gateway()).execute(command())
    assert isinstance(result, Ok)
    review = result.value
    assert (review.id, review.run_id, review.reviewer_id) == (ReviewId(UUID(int=4)), RUN, REVIEWER)
    assert review.decision == FinalDecision.CLOSE
    assert [item.conclusion_id for item in review.conclusions] == [CONCLUSION]


@pytest.mark.asyncio
async def test_review_id_collision_is_reported_without_closing_the_run() -> None:
    store = gateway()
    accepted = await use_case(store).execute(command())
    assert isinstance(accepted, Ok)
    store.targets[RUN] = ReviewTarget(
        RUN, 4, (ReviewConclusion(CONCLUSION, "Original autoritatif"),)
    )
    store.closed_runs.clear()
    again = await store.commit(accepted.value, expected_revision=4)
    assert error_code(again) == ReviewErrorCode.REVIEW_ID_CONFLICT


@pytest.mark.asyncio
async def test_commit_failure_leaves_the_review_target_open() -> None:
    store = gateway()
    store.fail_writes = True
    with pytest.raises(RuntimeError, match="storage"):
        await use_case(store).execute(command())
    assert RUN in store.targets and store.reviews == {} and store.closed_runs == set()


@pytest.mark.asyncio
async def test_commit_checks_revision_again_after_read() -> None:
    store = gateway()
    accepted = await use_case(store).execute(command())
    assert isinstance(accepted, Ok)
    stale = await store.commit(accepted.value, expected_revision=4)
    assert error_code(stale) == ReviewErrorCode.CONFLICT


def test_commands_and_the_use_case_are_immutable() -> None:
    request = command()
    for target, field in (
        (request, "decision"),
        (request.conclusions[0], "action"),
        (use_case(gateway()), "clock"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, None)
