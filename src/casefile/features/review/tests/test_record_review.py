from datetime import UTC, datetime
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
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok

RUN = RunId(UUID(int=1))
CONCLUSION = ConclusionId(UUID(int=2))
REVIEWER = ReviewerId(UUID(int=3))
AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


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


def use_case(store: InMemoryReviewGateway, *, authorized: bool = True) -> RecordReview:
    access = InMemoryReviewerAccess({(REVIEWER, RUN)} if authorized else set())
    return RecordReview(store, access, FixedClock())


@pytest.mark.asyncio
async def test_review_uses_persisted_originals_and_commits_once() -> None:
    store = gateway()
    result = await use_case(store).execute(command())
    assert isinstance(result, Ok)
    assert result.value.conclusions[0].original_statement == "Original autoritatif"
    assert result.value.submitted_at == AT
    assert store.reviews[result.value.id] == result.value
    assert store.closed_runs == {RUN}
    assert isinstance(await use_case(store).execute(command()), Err)
    assert len(store.reviews) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "unauthorized",
        "unconfirmed",
        "stale",
        "missing",
        "empty",
        "duplicate",
        "unknown",
        "bad_correction",
    ],
)
async def test_invalid_review_has_no_commit(mode: str) -> None:
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
    assert isinstance(result, Err)
    assert store.reviews == {} and store.closed_runs == set()


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
    assert isinstance(stale, Err)
    assert stale.error.code == ReviewErrorCode.CONFLICT
