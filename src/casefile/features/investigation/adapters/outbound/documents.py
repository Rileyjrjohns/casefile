"""Versioned JSON snapshots, validated before domain reconstitution."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.features.investigation.domain.run import HumanDecision, Run, RunState
from casefile.shared.ids import CaseId, ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Result


def stored[T, E](result: Result[T, E]) -> T:
    if isinstance(result, Err):
        raise ValueError(f"Corrupt stored run: {result.error}")
    return result.value


class Document(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", frozen=True)


class BudgetData(Document):
    tool_calls: int
    tokens: int
    estimated_cost: Decimal


class ConfidenceData(Document):
    value: float


class SourceData(Document):
    tool: str
    record_id: str

    def to_domain(self) -> SourceReference:
        return stored(SourceReference.create(self.tool, self.record_id))


class ConclusionData(Document):
    id: UUID
    statement: str
    confidence: ConfidenceData
    sources: tuple[SourceData, ...]

    def to_domain(self) -> Conclusion:
        return stored(
            Conclusion.create(
                ConclusionId(self.id),
                self.statement,
                stored(Confidence.create(self.confidence.value)),
                tuple(item.to_domain() for item in self.sources),
            )
        )


class AnalysisData(Document):
    summary: str
    conclusions: tuple[ConclusionData, ...]
    recommendation: Recommendation

    def to_domain(self) -> Analysis:
        return stored(
            Analysis.create(
                self.summary,
                tuple(item.to_domain() for item in self.conclusions),
                self.recommendation,
            )
        )


class HumanData(Document):
    run_id: UUID
    review_id: UUID
    reviewer_id: UUID
    conclusion_ids: tuple[UUID, ...]
    recommendation: Recommendation
    decided_at: datetime

    def to_domain(self) -> HumanDecision:
        return stored(
            HumanDecision.create(
                RunId(self.run_id),
                ReviewId(self.review_id),
                ReviewerId(self.reviewer_id),
                tuple(ConclusionId(id) for id in self.conclusion_ids),
                self.recommendation,
                self.decided_at,
                confirmed=True,
            )
        )


class RunDocument(Document):
    schema_version: Literal[1] = 1
    id: UUID
    case_id: UUID
    state: RunState
    limits: BudgetData
    usage: BudgetData
    sources: tuple[SourceData, ...]
    analysis: AnalysisData | None
    human_decision: HumanData | None
    failure_reason: str | None

    def to_domain(self) -> Run:
        limits = stored(
            BudgetLimits.create(
                self.limits.tool_calls, self.limits.tokens, self.limits.estimated_cost
            )
        )
        run = Run.new(RunId(self.id), CaseId(self.case_id), limits)
        if self.state != RunState.PENDING:
            run = stored(run.start_gathering())
            run = stored(run.record_sources(tuple(item.to_domain() for item in self.sources)))
            if (
                self.state in {RunState.ANALYZING, RunState.AWAITING_REVIEW, RunState.CLOSED}
                or self.analysis is not None
            ):
                run = stored(run.start_analysis())
            run = stored(
                run.consume(
                    stored(
                        BudgetUsage.create(
                            self.usage.tool_calls, self.usage.tokens, self.usage.estimated_cost
                        )
                    )
                )
            )
            if self.analysis is not None:
                run = stored(run.submit_analysis(self.analysis.to_domain()))
            if self.human_decision is not None:
                run = stored(run.close(self.human_decision.to_domain()))
            if self.state == RunState.FAILED:
                run = stored(run.fail(self.failure_reason or ""))
        if RunDocument.model_validate(run) != self:
            raise ValueError("Stored run fields violate the state invariants")
        return run


def encode_run(run: Run) -> dict[str, object]:
    return RunDocument.model_validate(run).model_dump(mode="json")


def decode_run(payload: dict[str, object]) -> Run:
    return RunDocument.model_validate(payload).to_domain()
