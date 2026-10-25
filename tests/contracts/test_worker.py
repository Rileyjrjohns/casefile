import asyncio
from datetime import timedelta

import pytest

from casefile.features.investigation.application.jobs import JobLease, JobQueue
from casefile.features.investigation.application.worker import LeaseLostError, RunWorker
from tests.contracts.support import RUN, Backend, seed

pytestmark = pytest.mark.asyncio


class RecordingExecutor:
    def __init__(
        self, *, failure: bool = False, block: bool = False, release: JobQueue | None = None
    ) -> None:
        self.seen: list[JobLease] = []
        self.failure = failure
        self.block = block
        self.release = release

    async def execute(self, lease: JobLease) -> None:
        self.seen.append(lease)
        if self.failure:
            raise RuntimeError("Executor unavailable")
        if self.block:
            await asyncio.Event().wait()
        if self.release is not None:
            await self.release.retry(lease, reason="Lease ownership changed", delay=timedelta(0))


async def test_worker_acknowledges_only_after_execution(backend: Backend) -> None:
    executor = RecordingExecutor()
    worker = RunWorker(backend.queue, executor)
    assert not await worker.run_once()
    await seed(backend)
    assert await worker.run_once()
    assert executor.seen[0].run_id == RUN
    assert not await worker.run_once()


async def test_worker_requeues_technical_failures_and_propagates_them(backend: Backend) -> None:
    await seed(backend)
    executor = RecordingExecutor(failure=True)
    worker = RunWorker(backend.queue, executor, retry_delay=timedelta(0))
    with pytest.raises(RuntimeError, match="Executor unavailable"):
        await worker.run_once()
    executor.failure = False
    assert await worker.run_once()
    assert [lease.attempt for lease in executor.seen] == [1, 2]


async def test_worker_timeout_releases_owned_job(backend: Backend) -> None:
    await seed(backend)
    worker = RunWorker(
        backend.queue,
        RecordingExecutor(block=True),
        execution_timeout=timedelta(milliseconds=20),
        retry_delay=timedelta(0),
    )
    with pytest.raises(TimeoutError):
        await worker.run_once()
    fresh = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert fresh is not None and fresh.attempt == 2


async def test_worker_detects_lost_ownership(backend: Backend) -> None:
    await seed(backend)
    with pytest.raises(LeaseLostError):
        await RunWorker(backend.queue, RecordingExecutor(release=backend.queue)).run_once()


async def test_queue_rejects_invalid_configuration(backend: Backend) -> None:
    with pytest.raises(ValueError):
        await backend.queue.claim(lease_for=timedelta(0))
    with pytest.raises(ValueError):
        RunWorker(backend.queue, RecordingExecutor(), execution_timeout=timedelta(seconds=60))
    await seed(backend)
    lease = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None
    with pytest.raises(ValueError):
        await backend.queue.retry(lease, reason="", delay=timedelta(0))
