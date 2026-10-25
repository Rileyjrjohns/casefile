from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Text, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from casefile.features.case_intake.domain.case import AlertKind, Case
from casefile.features.case_intake.domain.errors import IntakeError, IntakeErrorCode
from casefile.shared.ids import CaseId
from casefile.shared.result import Err, Ok, Result


class IntakeBase(DeclarativeBase):
    pass


class CaseRow(IntakeBase):
    __tablename__ = "cases"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    alert_id: Mapped[str] = mapped_column(Text, unique=True)
    subject_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_domain(self) -> Case:
        result = Case.create(
            CaseId(self.id),
            self.alert_id,
            self.subject_id,
            AlertKind(self.kind),
            self.description,
            self.opened_at,
        )
        if isinstance(result, Err):
            raise ValueError(f"Corrupt stored case: {result.error}")
        return result.value


class SqlCaseRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, case_id: CaseId) -> Case | None:
        async with self.sessions() as session:
            row = await session.get(CaseRow, case_id)
            return row.to_domain() if row is not None else None

    async def list(self, *, limit: int = 50, offset: int = 0) -> tuple[Case, ...]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(CaseRow)
                .order_by(CaseRow.opened_at.desc(), CaseRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return tuple(row.to_domain() for row in rows)

    async def add(self, case: Case) -> Result[Case, IntakeError]:
        async with self.sessions.begin() as session:
            inserted = await session.scalar(
                insert(CaseRow)
                .values(
                    id=case.id,
                    alert_id=case.alert_id,
                    subject_id=case.subject_id,
                    kind=case.kind.value,
                    description=case.description,
                    opened_at=case.opened_at,
                )
                .on_conflict_do_nothing()
                .returning(CaseRow.id)
            )
            if inserted is not None:
                return Ok(case)
            same_id = await session.scalar(select(CaseRow.id).where(CaseRow.id == case.id))
            code = (
                IntakeErrorCode.CASE_ALREADY_EXISTS
                if same_id is not None
                else IntakeErrorCode.ALERT_ALREADY_EXISTS
            )
            return Err(IntakeError(code, "Case or alert already exists."))
