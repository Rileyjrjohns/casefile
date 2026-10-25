"""Deterministic closure constraints, independent of provider confidence or prompts."""

from dataclasses import dataclass

from casefile.features.investigation.application.agent_types import Signal
from casefile.features.investigation.application.banking_policy import is_concern
from casefile.features.investigation.domain.analysis import Recommendation


@dataclass(frozen=True, slots=True)
class ClosureConstraint:
    recommendation: Recommendation
    blocking_signals: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def counts_as_concern(signal: Signal) -> bool:
    """Whether a signal blocks closure: any finding, or a risk estimate at half confidence."""
    return signal.confidence >= 0.5 if signal.key.startswith("risk:") else is_concern(signal)


def constrain_closure(
    proposed: Recommendation,
    signals: tuple[Signal, ...],
    *,
    has_profile: bool,
    evidence_gaps: bool = False,
) -> ClosureConstraint:
    if proposed != Recommendation.CLOSE:
        return ClosureConstraint(proposed)
    concerns = tuple(signal.key for signal in signals if counts_as_concern(signal))
    gaps = evidence_gaps or any(signal.key.startswith("gap:") for signal in signals)
    reasons = (
        *(("unresolved_concerns",) if concerns else ()),
        *(("missing_kyc",) if not has_profile else ()),
        *(("evidence_gaps",) if gaps else ()),
    )
    recommendation = (
        Recommendation.ESCALATE
        if concerns
        else Recommendation.REQUEST_INFORMATION
        if reasons
        else proposed
    )
    return ClosureConstraint(recommendation, concerns, reasons)
