from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from casefile.shared.ids import RunId


@dataclass(frozen=True, slots=True)
class JobLease:
    run_id: RunId
    token: UUID
    attempt: int
    expires_at: datetime


class JobQueue(Protocol):
    async def claim(self, *, lease_for: timedelta) -> JobLease | None: ...
    async def ack(self, lease: JobLease) -> bool: ...
    async def retry(self, lease: JobLease, *, reason: str, delay: timedelta) -> bool: ...


class RunExecutor(Protocol):
    async def execute(self, lease: JobLease) -> None:
        """Execute/resume one run; operations must tolerate at-least-once delivery."""
        ...
