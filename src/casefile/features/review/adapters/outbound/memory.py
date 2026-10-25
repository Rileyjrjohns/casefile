from casefile.features.review.application.ports import ReviewTarget
from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.features.review.domain.review import Review
from casefile.shared.ids import ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result


class InMemoryReviewerAccess:
    def __init__(self, allowed: set[tuple[ReviewerId, RunId]] | None = None) -> None:
        self.allowed = set(allowed or ())

    async def can_review(self, reviewer_id: ReviewerId, run_id: RunId) -> bool:
        return (reviewer_id, run_id) in self.allowed


class InMemoryReviewStorage:
    def __init__(self) -> None:
        self.reviews: dict[ReviewId, Review] = {}
        self.fail_writes = False

    async def get(self, review_id: ReviewId) -> Review | None:
        return self.reviews.get(review_id)


class InMemoryReviewGateway(InMemoryReviewStorage):
    """Standalone fake for review use cases; bootstrap supplies the connected adapter."""

    def __init__(self) -> None:
        super().__init__()
        self.targets: dict[RunId, ReviewTarget] = {}
        self.closed_runs: set[RunId] = set()

    async def load(self, run_id: RunId) -> Result[ReviewTarget, ReviewError]:
        if run_id in self.closed_runs:
            return Err(ReviewError(ReviewErrorCode.RUN_NOT_REVIEWABLE, "Run is already closed."))
        target = self.targets.get(run_id)
        if target is None:
            return Err(ReviewError(ReviewErrorCode.RUN_NOT_FOUND, "Run does not exist."))
        return Ok(target)

    async def commit(
        self, review: Review, *, expected_revision: int
    ) -> Result[Review, ReviewError]:
        target = self.targets.get(review.run_id)
        if (
            target is None
            or target.revision != expected_revision
            or review.run_id in self.closed_runs
        ):
            return Err(
                ReviewError(
                    ReviewErrorCode.CONFLICT, "Run is no longer available at this revision."
                )
            )
        if review.id in self.reviews:
            return Err(ReviewError(ReviewErrorCode.REVIEW_ID_CONFLICT, "Review ID already exists."))
        originals = {item.id: item.statement for item in target.conclusions}
        if originals != {
            item.conclusion_id: item.original_statement for item in review.conclusions
        }:
            return Err(
                ReviewError(ReviewErrorCode.CONFLICT, "Review originals do not match the run.")
            )
        if self.fail_writes:
            raise RuntimeError("Simulated review storage failure")
        self.reviews[review.id] = review
        self.closed_runs.add(review.run_id)
        del self.targets[review.run_id]
        return Ok(review)
