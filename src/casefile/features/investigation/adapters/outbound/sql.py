from datetime import datetime
from uuid import UUID

from psycopg.errors import ForeignKeyViolation
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from casefile.features.investigation.adapters.outbound.documents import decode_run, encode_run
from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Err, Ok, Result


class InvestigationBase(DeclarativeBase):
    pass


class RunRow(InvestigationBase):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_runs_revision"),
        CheckConstraint(
            "state IN ('PENDING','GATHERING','ANALYZING','AWAITING_REVIEW','CLOSED','FAILED')",
            name="ck_runs_state",
        ),
        CheckConstraint(
            "payload->>'state' = state AND payload->>'id' = id::text "
            "AND payload->>'case_id' = case_id::text",
            name="ck_runs_payload",
        ),
        Index(
            "uq_runs_active_case",
            "case_id",
            unique=True,
            postgresql_where=text("state NOT IN ('CLOSED','FAILED')"),
        ),
        Index("ix_runs_case_created_order", "case_id", "created_order"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(Text)
    revision: Mapped[int]
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_order: Mapped[int] = mapped_column(BigInteger, Identity(always=True))

    def snapshot(self) -> RunSnapshot:
        run = decode_run(self.payload)
        if run.id != self.id or run.case_id != self.case_id or run.state.value != self.state:
            raise ValueError("Run columns disagree with its stored document")
        return RunSnapshot(run, self.revision)


class JobRow(InvestigationBase):
    __tablename__ = "run_jobs"
    __table_args__ = (
        CheckConstraint("state IN ('READY','RUNNING','DONE')", name="ck_jobs_state"),
        CheckConstraint("attempt >= 0", name="ck_jobs_attempt"),
        CheckConstraint(
            "(state = 'RUNNING') = (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_jobs_lease",
        ),
        Index("ix_jobs_claim", "state", "available_at"),
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", name="fk_jobs_run"), primary_key=True
    )
    state: Mapped[str] = mapped_column(Text, server_default="READY")
    attempt: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class SqlRunStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, run_id: RunId) -> RunSnapshot | None:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
            return row.snapshot() if row is not None else None

    async def for_case(self, case_id: CaseId) -> tuple[RunSnapshot, ...]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(RunRow)
                .where(RunRow.case_id == case_id)
                .order_by(RunRow.created_order.desc(), RunRow.id.desc())
            )
            return tuple(row.snapshot() for row in rows)

    async def enqueue(self, run: Run) -> Result[Run, InvestigationError]:
        if run.state != RunState.PENDING:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_TRANSITION, "Only pending runs may be queued."
                )
            )
        try:
            async with self.sessions.begin() as session:
                inserted = await session.scalar(
                    insert(RunRow)
                    .values(
                        id=run.id,
                        case_id=run.case_id,
                        state=run.state.value,
                        revision=1,
                        payload=encode_run(run),
                    )
                    .on_conflict_do_nothing()
                    .returning(RunRow.id)
                )
                if inserted is None:
                    existing = await session.get(RunRow, run.id)
                    code = (
                        InvestigationErrorCode.RUN_ALREADY_EXISTS
                        if existing is not None
                        else InvestigationErrorCode.ACTIVE_RUN_EXISTS
                    )
                    return Err(InvestigationError(code, "Run ID or active case already exists."))
                session.add(JobRow(run_id=run.id))
            return Ok(run)
        except IntegrityError as error:
            if (
                isinstance(error.orig, ForeignKeyViolation)
                and error.orig.diag.constraint_name == "fk_runs_case"
            ):
                return Err(
                    InvestigationError(
                        InvestigationErrorCode.CASE_NOT_FOUND, "Case does not exist."
                    )
                )
            raise

    async def save(
        self, run: Run, *, expected_revision: int
    ) -> Result[RunSnapshot, InvestigationError]:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(RunRow).where(RunRow.id == run.id).with_for_update())
            if row is None:
                return Err(
                    InvestigationError(InvestigationErrorCode.RUN_NOT_FOUND, "Run does not exist.")
                )
            if row.revision != expected_revision or row.case_id != run.case_id:
                return Err(
                    InvestigationError(
                        InvestigationErrorCode.CONFLICT, "Run changed since it was read."
                    )
                )
            previous = row.snapshot().run
            progression = tuple(RunState)
            if (
                run.state == RunState.CLOSED
                or run.human_decision != previous.human_decision
                or progression.index(run.state) < progression.index(previous.state)
            ):
                return Err(
                    InvestigationError(
                        InvestigationErrorCode.INVALID_TRANSITION,
                        "Runs cannot move backwards or bypass atomic review finalization.",
                    )
                )
            row.payload = encode_run(run)
            row.state = run.state.value
            row.revision += 1
            return Ok(RunSnapshot(run, row.revision))
