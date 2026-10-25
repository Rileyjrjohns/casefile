from dataclasses import dataclass
from enum import StrEnum


class EvaluationErrorCode(StrEnum):
    ACCESS_DENIED = "access_denied"
    CORRECTION_NOT_FOUND = "correction_not_found"
    NOT_A_CORRECTION = "not_a_correction"
    INVALID_REFERENCE = "invalid_reference"
    REFERENCE_ID_CONFLICT = "reference_id_conflict"


@dataclass(frozen=True, slots=True)
class EvaluationError:
    code: EvaluationErrorCode
    message: str
