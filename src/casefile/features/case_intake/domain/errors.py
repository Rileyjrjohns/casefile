from dataclasses import dataclass
from enum import StrEnum


class IntakeErrorCode(StrEnum):
    INVALID_CASE = "invalid_case"
    CASE_ALREADY_EXISTS = "case_already_exists"
    ALERT_ALREADY_EXISTS = "alert_already_exists"


@dataclass(frozen=True, slots=True)
class IntakeError:
    code: IntakeErrorCode
    message: str
