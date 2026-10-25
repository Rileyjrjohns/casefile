from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from casefile.features.review.domain.review import (
    ConclusionReview,
    FinalDecision,
    Review,
    ReviewAction,
)
from casefile.shared.ids import ConclusionId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Result


def stored[T, E](result: Result[T, E]) -> T:
    if isinstance(result, Err):
        raise ValueError(f"Corrupt stored review: {result.error}")
    return result.value


class ConclusionData(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    conclusion_id: UUID
    original_statement: str
    action: ReviewAction
    corrected_statement: str | None
    reason: str | None

    def to_domain(self) -> ConclusionReview:
        return stored(
            ConclusionReview.create(
                ConclusionId(self.conclusion_id),
                self.original_statement,
                self.action,
                corrected_statement=self.corrected_statement,
                reason=self.reason,
            )
        )


class ReviewDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    schema_version: Literal[1] = 1
    id: UUID
    run_id: UUID
    reviewer_id: UUID
    conclusions: tuple[ConclusionData, ...]
    decision: FinalDecision
    submitted_at: datetime

    def to_domain(self) -> Review:
        conclusions = tuple(item.to_domain() for item in self.conclusions)
        return stored(
            Review.submit(
                ReviewId(self.id),
                RunId(self.run_id),
                ReviewerId(self.reviewer_id),
                tuple(item.conclusion_id for item in conclusions),
                conclusions,
                self.decision,
                self.submitted_at,
                confirmed=True,
            )
        )


class ReviewBase(DeclarativeBase):
    pass


class ReviewRow(ReviewBase):
    __tablename__ = "reviews"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(unique=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)

    def to_domain(self) -> Review:
        review = ReviewDocument.model_validate(self.payload).to_domain()
        if review.id != self.id or review.run_id != self.run_id:
            raise ValueError("Review columns disagree with its stored document")
        return review


class SqlReviewReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, review_id: ReviewId) -> Review | None:
        async with self.sessions() as session:
            row = await session.get(ReviewRow, review_id)
            return row.to_domain() if row is not None else None
