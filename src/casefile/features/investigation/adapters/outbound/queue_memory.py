from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from casefile.features.investigation.application.jobs import JobLease
from casefile.shared.ids import RunId

EARLIEST = datetime.min.replace(tzinfo=UTC)


@dataclass
class MemoryJob:
    attempt: int = 0
    lease: JobLease | None = None
    available_at: datetime = EARLIEST
    done: bool = False
    last_error: str | None = None


class InMemoryJobQueue:
    def __init__(self, pending: list[RunId]) -> None:
        self.pending = pending
        self.records: dict[RunId, MemoryJob] = {}

    async def claim(self, *, lease_for: timedelta) -> JobLease | None:
        if lease_for <= timedelta(0):
            raise ValueError("Lease duration must be positive")
        now = datetime.now(UTC)
        for run_id in self.pending:
            job = self.records.setdefault(run_id, MemoryJob())
            if job.done or job.available_at > now or (job.lease and job.lease.expires_at > now):
                continue
            job.attempt += 1
            job.lease = JobLease(run_id, uuid4(), job.attempt, now + lease_for)
            return job.lease
        return None

    def _owned(self, lease: JobLease) -> MemoryJob | None:
        job = self.records.get(lease.run_id)
        if (
            job is None
            or job.done
            or job.lease is None
            or job.lease.token != lease.token
            or job.lease.expires_at <= datetime.now(UTC)
        ):
            return None
        return job

    async def ack(self, lease: JobLease) -> bool:
        job = self._owned(lease)
        if job is None:
            return False
        job.done = True
        job.lease = None
        return True

    def owns(self, lease: JobLease) -> bool:
        return self._owned(lease) is not None

    async def retry(self, lease: JobLease, *, reason: str, delay: timedelta) -> bool:
        if not reason.strip() or delay < timedelta(0):
            raise ValueError("Retry needs a reason and a nonnegative delay")
        job = self._owned(lease)
        if job is None:
            return False
        job.lease = None
        job.last_error = reason
        job.available_at = datetime.now(UTC) + delay
        return True
