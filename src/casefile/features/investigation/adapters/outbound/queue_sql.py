from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casefile.features.investigation.adapters.outbound.sql import JobRow
from casefile.features.investigation.application.jobs import JobLease
from casefile.shared.ids import RunId


class SqlJobQueue:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def claim(self, *, lease_for: timedelta) -> JobLease | None:
        if lease_for <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        async with self.sessions.begin() as session:
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            row = await session.scalar(
                select(JobRow)
                .where(
                    or_(
                        and_(JobRow.state == "READY", JobRow.available_at <= now),
                        and_(JobRow.state == "RUNNING", JobRow.lease_until <= now),
                    )
                )
                .order_by(JobRow.available_at, JobRow.run_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            row.state = "RUNNING"
            row.token = uuid4()
            row.attempt += 1
            row.lease_until = now + lease_for
            return JobLease(RunId(row.run_id), row.token, row.attempt, row.lease_until)

    async def ack(self, lease: JobLease) -> bool:
        async with self.sessions.begin() as session:
            claimed = await session.scalar(
                update(JobRow)
                .where(
                    JobRow.run_id == lease.run_id,
                    JobRow.state == "RUNNING",
                    JobRow.token == lease.token,
                    JobRow.lease_until > func.clock_timestamp(),
                )
                .values(state="DONE", token=None, lease_until=None)
                .returning(JobRow.run_id)
            )
            return claimed is not None

    async def retry(self, lease: JobLease, *, reason: str, delay: timedelta) -> bool:
        if not reason.strip() or delay < timedelta(0):
            raise ValueError("Retry needs a reason and a nonnegative delay")
        async with self.sessions.begin() as session:
            claimed = await session.scalar(
                update(JobRow)
                .where(
                    JobRow.run_id == lease.run_id,
                    JobRow.state == "RUNNING",
                    JobRow.token == lease.token,
                    JobRow.lease_until > func.clock_timestamp(),
                )
                .values(
                    state="READY",
                    token=None,
                    lease_until=None,
                    available_at=func.clock_timestamp() + delay,
                    last_error=reason,
                )
                .returning(JobRow.run_id)
            )
            return claimed is not None
