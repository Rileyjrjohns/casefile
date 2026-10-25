from dataclasses import dataclass
from enum import StrEnum


class InvestigationErrorCode(StrEnum):
    CASE_NOT_FOUND = "case_not_found"
    RUN_ALREADY_EXISTS = "run_already_exists"
    ACTIVE_RUN_EXISTS = "active_run_exists"
    RUN_NOT_FOUND = "run_not_found"
    CONFLICT = "conflict"
    INVALID_CONFIDENCE = "invalid_confidence"
    INVALID_SOURCE = "invalid_source"
    INVALID_CONCLUSION = "invalid_conclusion"
    INVALID_ANALYSIS = "invalid_analysis"
    INVALID_BUDGET = "invalid_budget"
    INVALID_USAGE = "invalid_usage"
    INVALID_TRANSITION = "invalid_transition"
    UNKNOWN_SOURCE = "unknown_source"
    HUMAN_DECISION_REQUIRED = "human_decision_required"
    INVALID_HUMAN_DECISION = "invalid_human_decision"
    REVIEW_MISMATCH = "review_mismatch"
    INVALID_FAILURE_REASON = "invalid_failure_reason"


@dataclass(frozen=True, slots=True)
class InvestigationError:
    code: InvestigationErrorCode
    message: str
