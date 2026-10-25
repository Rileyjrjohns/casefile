from dataclasses import dataclass
from enum import StrEnum


class ReviewErrorCode(StrEnum):
    ACCESS_DENIED = "access_denied"
    RUN_NOT_FOUND = "run_not_found"
    RUN_NOT_REVIEWABLE = "run_not_reviewable"
    UNKNOWN_CONCLUSION = "unknown_conclusion"
    CONFLICT = "conflict"
    REVIEW_ID_CONFLICT = "review_id_conflict"
    INVALID_CONCLUSION_REVIEW = "invalid_conclusion_review"
    CONFIRMATION_REQUIRED = "confirmation_required"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INCOMPLETE_REVIEW = "incomplete_review"


@dataclass(frozen=True, slots=True)
class ReviewError:
    code: ReviewErrorCode
    message: str
