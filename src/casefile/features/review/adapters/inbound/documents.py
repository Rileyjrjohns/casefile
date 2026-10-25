from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from casefile.features.review.application.record_review import RecordReviewCommand, ReviewInput
from casefile.features.review.domain.review import FinalDecision, ReviewAction
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId


class ConclusionReviewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conclusion_id: UUID
    action: ReviewAction
    corrected_statement: Annotated[str | None, Field(max_length=4000)] = None
    reason: Annotated[str | None, Field(max_length=2000)] = None


class ReviewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    expected_revision: Annotated[int, Field(ge=1, strict=True)]
    conclusions: Annotated[tuple[ConclusionReviewDocument, ...], Field(max_length=100)]
    decision: FinalDecision
    confirmed: StrictBool = False

    def command(self, run_id: RunId, reviewer: ReviewerId) -> RecordReviewCommand:
        return RecordReviewCommand(
            ReviewId(self.id),
            run_id,
            reviewer,
            self.expected_revision,
            tuple(
                ReviewInput(
                    ConclusionId(item.conclusion_id),
                    item.action,
                    item.corrected_statement,
                    item.reason,
                )
                for item in self.conclusions
            ),
            self.decision,
            self.confirmed,
        )
