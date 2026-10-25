from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg.errors import ForeignKeyViolation
from pydantic import BaseModel, ConfigDict
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.features.evaluation.domain.reference import Correction, ReferenceExample
from casefile.shared.ids import CaseId, ConclusionId, ReferenceId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result


class CorrectionData(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    review_id: UUID
    run_id: UUID
    case_id: UUID
    conclusion_id: UUID
    original_statement: str
    corrected_statement: str
    reason: str
    reviewer_id: UUID
    reviewed_at: datetime

    def to_domain(self) -> Correction:
        return Correction(
            ReviewId(self.review_id),
            RunId(self.run_id),
            CaseId(self.case_id),
            ConclusionId(self.conclusion_id),
            self.original_statement,
            self.corrected_statement,
            self.reason,
            ReviewerId(self.reviewer_id),
            self.reviewed_at,
        )


class ReferenceDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    schema_version: Literal[1] = 1
    id: UUID
    correction: CorrectionData
    promoted_by: UUID
    promoted_at: datetime

    def to_domain(self) -> ReferenceExample:
        result = ReferenceExample.create(
            ReferenceId(self.id),
            self.correction.to_domain(),
            ReviewerId(self.promoted_by),
            self.promoted_at,
        )
        if isinstance(result, Err):
            raise ValueError(f"Corrupt stored reference: {result.error}")
        return result.value


class EvaluationBase(DeclarativeBase):
    pass


class ReferenceRow(EvaluationBase):
    __tablename__ = "reference_examples"
    __table_args__ = (UniqueConstraint("review_id", "conclusion_id", name="uq_reference_source"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    review_id: Mapped[UUID]
    conclusion_id: Mapped[UUID]
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)

    def to_domain(self) -> ReferenceExample:
        example = ReferenceDocument.model_validate(self.payload).to_domain()
        if (
            example.id != self.id
            or example.correction.review_id != self.review_id
            or example.correction.conclusion_id != self.conclusion_id
        ):
            raise ValueError("Reference columns disagree with its stored document")
        return example


class SqlReferenceRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, reference_id: ReferenceId) -> ReferenceExample | None:
        async with self.sessions() as session:
            row = await session.get(ReferenceRow, reference_id)
            return row.to_domain() if row is not None else None

    async def add_once(
        self, example: ReferenceExample
    ) -> Result[ReferenceExample, EvaluationError]:
        try:
            async with self.sessions.begin() as session:
                inserted = await session.scalar(
                    insert(ReferenceRow)
                    .values(
                        id=example.id,
                        review_id=example.correction.review_id,
                        conclusion_id=example.correction.conclusion_id,
                        payload=ReferenceDocument.model_validate(example).model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                    .returning(ReferenceRow.id)
                )
                if inserted is not None:
                    return Ok(example)
                existing = await session.scalar(
                    select(ReferenceRow).where(
                        ReferenceRow.review_id == example.correction.review_id,
                        ReferenceRow.conclusion_id == example.correction.conclusion_id,
                    )
                )
                if existing is not None:
                    return Ok(existing.to_domain())
                return Err(
                    EvaluationError(
                        EvaluationErrorCode.REFERENCE_ID_CONFLICT,
                        "Reference ID belongs to another correction.",
                    )
                )
        except IntegrityError as error:
            if (
                isinstance(error.orig, ForeignKeyViolation)
                and error.orig.diag.constraint_name == "fk_reference_review"
            ):
                return Err(
                    EvaluationError(
                        EvaluationErrorCode.CORRECTION_NOT_FOUND, "Review does not exist."
                    )
                )
            raise
