"""Lease-fenced transactions commit the run and its append-only trace together."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime
from sqlalchemy import CheckConstraint, ForeignKey, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from casefile.features.investigation.adapters.outbound.agent.journal import (
    conflict,
    valid_checkpoint,
)
from casefile.features.investigation.adapters.outbound.documents import (
    BudgetData,
    Document,
    encode_run,
    stored,
)
from casefile.features.investigation.adapters.outbound.sql import InvestigationBase, JobRow, RunRow
from casefile.features.investigation.application.agent_types import ExecutionSnapshot, TraceEvent
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationError
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import RunId
from casefile.shared.result import Ok, Result


class TraceDocument(Document):
    schema_version: Literal[1] = 1
    sequence: int
    operation: str
    phase: Literal["started", "succeeded", "failed", "transition"]
    at: AwareDatetime
    input_json: str
    output_json: str | None
    reserved: BudgetData | None
    actual: BudgetData | None
    error: str | None

    def to_event(self) -> TraceEvent:
        def usage(value: BudgetData | None) -> BudgetUsage | None:
            if value is None:
                return None
            return stored(BudgetUsage.create(value.tool_calls, value.tokens, value.estimated_cost))

        return TraceEvent(
            self.sequence,
            self.operation,
            self.phase,
            self.at,
            self.input_json,
            self.output_json,
            usage(self.reserved),
            usage(self.actual),
            self.error,
        )


class ExecutionEventRow(InvestigationBase):
    __tablename__ = "run_events"
    __table_args__ = (CheckConstraint("sequence > 0", name="ck_run_events_sequence"),)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", name="fk_run_events_run"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)

    def to_event(self) -> TraceEvent:
        event = TraceDocument.model_validate(self.payload).to_event()
        if event.sequence != self.sequence:
            raise ValueError("Trace sequence differs from its stored document.")
        return event


async def events_for(session: AsyncSession, run_id: RunId) -> tuple[TraceEvent, ...]:
    rows = await session.scalars(
        select(ExecutionEventRow)
        .where(
            ExecutionEventRow.run_id == run_id,
        )
        .order_by(ExecutionEventRow.sequence)
    )
    return tuple(row.to_event() for row in rows)


class SqlExecutionJournal:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def load(self, run_id: RunId) -> ExecutionSnapshot | None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RunRow).where(RunRow.id == run_id).with_for_update(read=True)
            )
            if row is None:
                return None
            return ExecutionSnapshot(row.snapshot(), await events_for(session, run_id))

    async def checkpoint(
        self,
        lease: JobLease,
        run: Run,
        event: TraceEvent,
        *,
        expected_revision: int,
    ) -> Result[ExecutionSnapshot, InvestigationError]:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(RunRow).where(RunRow.id == lease.run_id).with_for_update()
            )
            job = await session.scalar(
                select(JobRow).where(JobRow.run_id == lease.run_id).with_for_update()
            )
            now: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if (
                row is None
                or job is None
                or job.state != "RUNNING"
                or job.token != lease.token
                or job.lease_until is None
                or job.lease_until <= now
            ):
                return conflict()
            before = ExecutionSnapshot(row.snapshot(), await events_for(session, lease.run_id))
            if not valid_checkpoint(before, run, event, expected_revision):
                return conflict()
            row.payload = encode_run(run)
            row.state = run.state.value
            row.revision += 1
            await session.flush()
            session.add(
                ExecutionEventRow(
                    run_id=run.id,
                    sequence=event.sequence,
                    payload=TraceDocument.model_validate(event).model_dump(mode="json"),
                )
            )
            return Ok(ExecutionSnapshot(RunSnapshot(run, row.revision), (*before.events, event)))
