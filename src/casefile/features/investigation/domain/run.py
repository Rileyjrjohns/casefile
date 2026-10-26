"""Pure run aggregate: every transition returns a new immutable snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from casefile.features.investigation.domain.analysis import (
    Analysis,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.shared.ids import CaseId, ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


class RunState(StrEnum):
    PENDING = "PENDING"
    GATHERING = "GATHERING"
    ANALYZING = "ANALYZING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True, init=False)
class HumanDecision(FactoryOnly):
    """Receipt mapped from an authenticated review by the composition boundary."""

    run_id: RunId
    review_id: ReviewId
    reviewer_id: ReviewerId
    conclusion_ids: tuple[ConclusionId, ...]
    recommendation: Recommendation
    decided_at: datetime

    @classmethod
    def create(
        cls,
        run_id: RunId,
        review_id: ReviewId,
        reviewer_id: ReviewerId,
        conclusion_ids: tuple[ConclusionId, ...],
        recommendation: Recommendation,
        decided_at: datetime,
        *,
        confirmed: bool,
    ) -> Result["HumanDecision", InvestigationError]:
        if confirmed is not True:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.HUMAN_DECISION_REQUIRED,
                    "Explicit human confirmation is required.",
                )
            )
        if decided_at.utcoffset() is None or len(set(conclusion_ids)) != len(conclusion_ids):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_HUMAN_DECISION,
                    "A decision requires an aware timestamp and distinct conclusion identifiers.",
                )
            )
        return Ok(
            cls._new(
                run_id=run_id,
                review_id=review_id,
                reviewer_id=reviewer_id,
                conclusion_ids=tuple(conclusion_ids),
                recommendation=recommendation,
                decided_at=decided_at.astimezone(UTC),
            )
        )


@dataclass(frozen=True, slots=True, init=False)
class Run(FactoryOnly):
    id: RunId
    case_id: CaseId
    state: RunState
    limits: BudgetLimits
    usage: BudgetUsage
    sources: tuple[SourceReference, ...]
    analysis: Analysis | None
    human_decision: HumanDecision | None
    failure_reason: str | None

    @classmethod
    def new(cls, id: RunId, case_id: CaseId, limits: BudgetLimits) -> "Run":
        return cls._new(
            id=id,
            case_id=case_id,
            state=RunState.PENDING,
            limits=limits,
            usage=BudgetUsage.zero(),
            sources=(),
            analysis=None,
            human_decision=None,
            failure_reason=None,
        )

    def _invalid_transition(self, operation: str) -> Err[InvestigationError]:
        return Err(
            InvestigationError(
                InvestigationErrorCode.INVALID_TRANSITION, f"Cannot {operation} from {self.state}."
            )
        )

    def start_gathering(self) -> Result["Run", InvestigationError]:
        if self.state != RunState.PENDING:
            return self._invalid_transition("start gathering")
        return Ok(self._evolve(state=RunState.GATHERING))

    def record_sources(
        self, sources: tuple[SourceReference, ...]
    ) -> Result["Run", InvestigationError]:
        if self.state != RunState.GATHERING:
            return self._invalid_transition("record sources")
        return Ok(self._evolve(sources=tuple(dict.fromkeys((*self.sources, *sources)))))

    def start_analysis(self) -> Result["Run", InvestigationError]:
        if self.state != RunState.GATHERING:
            return self._invalid_transition("start analysis")
        return Ok(self._evolve(state=RunState.ANALYZING))

    def submit_analysis(self, analysis: Analysis) -> Result["Run", InvestigationError]:
        if self.state != RunState.ANALYZING:
            return self._invalid_transition("submit analysis")
        known_sources = set(self.sources)
        for conclusion in analysis.conclusions:
            for source in conclusion.sources:
                if source not in known_sources:
                    return Err(
                        InvestigationError(
                            InvestigationErrorCode.UNKNOWN_SOURCE,
                            f"Source not observed in this run: {source.tool}/{source.record_id}.",
                        )
                    )
        return Ok(self._evolve(state=RunState.AWAITING_REVIEW, analysis=analysis))

    def close(self, decision: HumanDecision | None) -> Result["Run", InvestigationError]:
        if self.state != RunState.AWAITING_REVIEW:
            return self._invalid_transition("close")
        if decision is None:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.HUMAN_DECISION_REQUIRED,
                    "A run cannot close without a human decision.",
                )
            )
        if self.analysis is None:
            # submit_analysis is the only way to reach AWAITING_REVIEW; a snapshot without an
            # analysis is corrupt and must not close.
            return self._invalid_transition("close without an analysis")
        if decision.run_id != self.id or set(decision.conclusion_ids) != {
            item.id for item in self.analysis.conclusions
        }:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.REVIEW_MISMATCH,
                    "The human decision must cover exactly this run and all its conclusions.",
                )
            )
        return Ok(self._evolve(state=RunState.CLOSED, human_decision=decision))

    def fail(self, reason: str) -> Result["Run", InvestigationError]:
        """Fail from any non-terminal state; CLOSED is terminal and FAILED keeps its first cause."""
        if not reason.strip():
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_FAILURE_REASON,
                    "A failed run must retain a nonempty reason.",
                )
            )
        if self.state == RunState.CLOSED:
            return self._invalid_transition("fail")
        if self.state == RunState.FAILED:
            return Ok(self)
        return Ok(self._evolve(state=RunState.FAILED, failure_reason=reason))

    def consume(self, increment: BudgetUsage) -> Result["Run", InvestigationError]:
        """Record usage; an overrun returns the updated FAILED snapshot in Ok."""
        if self.state not in {RunState.GATHERING, RunState.ANALYZING}:
            return self._invalid_transition("consume budget")
        usage = self.usage.plus(increment)
        exceeded = usage.exceeded_dimensions(self.limits)
        if exceeded:
            return Ok(
                self._evolve(
                    usage=usage,
                    state=RunState.FAILED,
                    failure_reason=f"Budget exceeded: {', '.join(exceeded)}.",
                )
            )
        return Ok(self._evolve(usage=usage))

    def reserve(self, increment: BudgetUsage) -> Result["Run", InvestigationError]:
        """Authorize an external call before it happens; a refused call consumes nothing."""
        if self.state not in {RunState.GATHERING, RunState.ANALYZING}:
            return self._invalid_transition("reserve budget")
        exceeded = self.usage.plus(increment).exceeded_dimensions(self.limits)
        if exceeded:
            return self.fail(f"Budget reservation exceeds: {', '.join(exceeded)}.")
        return self.consume(increment)

    def settle(
        self, reserved: BudgetUsage, actual: BudgetUsage
    ) -> Result["Run", InvestigationError]:
        """Replace a persisted reservation with reported usage; uncertain calls retain it."""
        if self.state not in {RunState.GATHERING, RunState.ANALYZING}:
            return self._invalid_transition("settle budget")
        released = self.usage.minus(reserved)
        if isinstance(released, Err):
            return released
        return self._evolve(usage=released.value).consume(actual)
