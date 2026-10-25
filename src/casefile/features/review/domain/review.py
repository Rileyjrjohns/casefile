"""Human review records, independent from investigation's domain objects."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


class ReviewAction(StrEnum):
    VALIDATE = "validate"
    CORRECT = "correct"
    REJECT = "reject"


class FinalDecision(StrEnum):
    CLOSE = "close"
    ESCALATE = "escalate"
    REQUEST_INFORMATION = "request_information"


@dataclass(frozen=True, slots=True, init=False)
class ConclusionReview(FactoryOnly):
    conclusion_id: ConclusionId
    original_statement: str
    action: ReviewAction
    corrected_statement: str | None
    reason: str | None

    @classmethod
    def create(
        cls,
        conclusion_id: ConclusionId,
        original_statement: str,
        action: ReviewAction,
        *,
        corrected_statement: str | None = None,
        reason: str | None = None,
    ) -> Result["ConclusionReview", ReviewError]:
        invalid = not original_statement.strip() or (reason is not None and not reason.strip())
        if action == ReviewAction.CORRECT:
            invalid = (
                invalid or not corrected_statement or not corrected_statement.strip() or not reason
            )
        else:
            invalid = invalid or corrected_statement is not None
            if action == ReviewAction.REJECT:
                invalid = invalid or not reason
        if invalid:
            return Err(
                ReviewError(
                    ReviewErrorCode.INVALID_CONCLUSION_REVIEW,
                    "Corrections need replacement text and a reason; rejections need a reason. "
                    "Only corrections may replace text.",
                )
            )
        return Ok(
            cls._new(
                conclusion_id=conclusion_id,
                original_statement=original_statement,
                action=action,
                corrected_statement=corrected_statement,
                reason=reason,
            )
        )


@dataclass(frozen=True, slots=True, init=False)
class Review(FactoryOnly):
    id: ReviewId
    run_id: RunId
    reviewer_id: ReviewerId
    conclusions: tuple[ConclusionReview, ...]
    decision: FinalDecision
    submitted_at: datetime

    @classmethod
    def submit(
        cls,
        id: ReviewId,
        run_id: RunId,
        reviewer_id: ReviewerId,
        expected_conclusion_ids: tuple[ConclusionId, ...],
        conclusions: tuple[ConclusionReview, ...],
        decision: FinalDecision,
        submitted_at: datetime,
        *,
        confirmed: bool,
    ) -> Result["Review", ReviewError]:
        if confirmed is not True:
            return Err(
                ReviewError(
                    ReviewErrorCode.CONFIRMATION_REQUIRED,
                    "Explicit human confirmation is required.",
                )
            )
        if submitted_at.utcoffset() is None:
            return Err(
                ReviewError(
                    ReviewErrorCode.INVALID_TIMESTAMP,
                    "The review timestamp must include a timezone.",
                )
            )
        expected = set(expected_conclusion_ids)
        reviewed = {item.conclusion_id for item in conclusions}
        if (
            len(expected) != len(expected_conclusion_ids)
            or len(reviewed) != len(conclusions)
            or expected != reviewed
        ):
            return Err(
                ReviewError(
                    ReviewErrorCode.INCOMPLETE_REVIEW,
                    "Review each expected conclusion exactly once, without omissions or additions.",
                )
            )
        return Ok(
            cls._new(
                id=id,
                run_id=run_id,
                reviewer_id=reviewer_id,
                conclusions=tuple(conclusions),
                decision=decision,
                submitted_at=submitted_at.astimezone(UTC),
            )
        )

    @property
    def corrections(self) -> tuple[ConclusionReview, ...]:
        return tuple(item for item in self.conclusions if item.action == ReviewAction.CORRECT)
