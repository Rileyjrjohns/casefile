from dataclasses import dataclass

from casefile.features.review.application.ports import ReviewerAccess, ReviewGateway
from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.shared.clock import Clock
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Result


@dataclass(frozen=True, slots=True)
class ReviewInput:
    conclusion_id: ConclusionId
    action: ReviewAction
    corrected_statement: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RecordReviewCommand:
    review_id: ReviewId
    run_id: RunId
    reviewer_id: ReviewerId
    expected_revision: int
    conclusions: tuple[ReviewInput, ...]
    decision: FinalDecision
    confirmed: bool


@dataclass(frozen=True, slots=True)
class RecordReview:
    gateway: ReviewGateway
    access: ReviewerAccess
    clock: Clock

    async def execute(self, command: RecordReviewCommand) -> Result[Review, ReviewError]:
        if not await self.access.can_review(command.reviewer_id, command.run_id):
            return Err(
                ReviewError(
                    ReviewErrorCode.ACCESS_DENIED, "Reviewer is not authorized for this run."
                )
            )
        if command.confirmed is not True:
            return Err(
                ReviewError(
                    ReviewErrorCode.CONFIRMATION_REQUIRED,
                    "Explicit human confirmation is required.",
                )
            )
        loaded = await self.gateway.load(command.run_id)
        if isinstance(loaded, Err):
            return loaded
        target = loaded.value
        if target.revision != command.expected_revision:
            return Err(ReviewError(ReviewErrorCode.CONFLICT, "Run changed since it was displayed."))
        originals = {item.id: item.statement for item in target.conclusions}
        decisions: list[ConclusionReview] = []
        for item in command.conclusions:
            if item.conclusion_id not in originals:
                return Err(
                    ReviewError(
                        ReviewErrorCode.UNKNOWN_CONCLUSION, "Conclusion is not part of this run."
                    )
                )
            decision = ConclusionReview.create(
                item.conclusion_id,
                originals[item.conclusion_id],
                item.action,
                corrected_statement=item.corrected_statement,
                reason=item.reason,
            )
            if isinstance(decision, Err):
                return decision
            decisions.append(decision.value)
        review = Review.submit(
            command.review_id,
            command.run_id,
            command.reviewer_id,
            tuple(originals),
            tuple(decisions),
            command.decision,
            self.clock.now(),
            confirmed=command.confirmed,
        )
        if isinstance(review, Err):
            return review
        return await self.gateway.commit(review.value, expected_revision=target.revision)
