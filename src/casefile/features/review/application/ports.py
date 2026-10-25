from dataclasses import dataclass
from typing import Protocol

from casefile.features.review.domain.errors import ReviewError
from casefile.features.review.domain.review import Review
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Result


@dataclass(frozen=True, slots=True)
class ReviewConclusion:
    id: ConclusionId
    statement: str


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    run_id: RunId
    revision: int
    conclusions: tuple[ReviewConclusion, ...]


class ReviewerAccess(Protocol):
    async def can_review(self, reviewer_id: ReviewerId, run_id: RunId) -> bool: ...


class ReviewReader(Protocol):
    async def get(self, review_id: ReviewId) -> Review | None: ...


class ReviewGateway(Protocol):
    async def load(self, run_id: RunId) -> Result[ReviewTarget, ReviewError]:
        """Read authoritative originals from a run awaiting review."""
        ...

    async def commit(
        self, review: Review, *, expected_revision: int
    ) -> Result[Review, ReviewError]:
        """Atomically store the review and close its run, or change neither.

        Recheck revision, state, originals and unique review ID at commit time.
        Technical failures propagate; they must leave both records unchanged.
        """
        ...
