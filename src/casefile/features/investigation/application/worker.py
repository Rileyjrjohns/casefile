import asyncio
from dataclasses import dataclass
from datetime import timedelta

from casefile.features.investigation.application.jobs import JobQueue, RunExecutor


class LeaseLostError(RuntimeError):
    """Another worker owns the job or the lease expired before acknowledgement."""


@dataclass(frozen=True, slots=True)
class RunWorker:
    queue: JobQueue
    executor: RunExecutor
    lease_for: timedelta = timedelta(seconds=60)
    execution_timeout: timedelta = timedelta(seconds=30)
    retry_delay: timedelta = timedelta(seconds=5)

    def __post_init__(self) -> None:
        if not timedelta(
            0
        ) < self.execution_timeout < self.lease_for or self.retry_delay < timedelta(0):
            raise ValueError("Execution timeout must be positive and shorter than the lease")

    async def run_once(self) -> bool:
        lease = await self.queue.claim(lease_for=self.lease_for)
        if lease is None:
            return False
        try:
            async with asyncio.timeout(self.execution_timeout.total_seconds()):
                await self.executor.execute(lease)
        except Exception as error:
            # Technical failures remain exceptions; release only the lease we own.
            released = await self.queue.retry(
                lease, reason=f"Executor failed: {type(error).__name__}", delay=self.retry_delay
            )
            if not released:
                raise LeaseLostError("Lease lost while retrying the job") from error
            raise
        if not await self.queue.ack(lease):
            raise LeaseLostError("Lease lost before job acknowledgement")
        return True
