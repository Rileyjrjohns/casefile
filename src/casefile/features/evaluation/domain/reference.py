from dataclasses import dataclass
from datetime import UTC, datetime

from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.shared.ids import CaseId, ConclusionId, ReferenceId, ReviewerId, ReviewId, RunId
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class Correction:
    review_id: ReviewId
    run_id: RunId
    case_id: CaseId
    conclusion_id: ConclusionId
    original_statement: str
    corrected_statement: str
    reason: str
    reviewer_id: ReviewerId
    reviewed_at: datetime


@dataclass(frozen=True, slots=True, init=False)
class ReferenceExample(FactoryOnly):
    """A curated correction candidate; exporting a complete golden case is separate."""

    id: ReferenceId
    correction: Correction
    promoted_by: ReviewerId
    promoted_at: datetime

    @classmethod
    def create(
        cls, id: ReferenceId, correction: Correction, promoted_by: ReviewerId, promoted_at: datetime
    ) -> Result["ReferenceExample", EvaluationError]:
        if (
            not all(
                text.strip()
                for text in (
                    correction.original_statement,
                    correction.corrected_statement,
                    correction.reason,
                )
            )
            or correction.reviewed_at.utcoffset() is None
            or promoted_at.utcoffset() is None
            or promoted_at < correction.reviewed_at
        ):
            return Err(
                EvaluationError(
                    EvaluationErrorCode.INVALID_REFERENCE,
                    "A reference needs the original, correction, reason "
                    "and consistent aware timestamps.",
                )
            )
        return Ok(
            cls._new(
                id=id,
                correction=correction,
                promoted_by=promoted_by,
                promoted_at=promoted_at.astimezone(UTC),
            )
        )
