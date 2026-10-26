"""A worker's timing contract: execution must end before the lease does."""

from datetime import timedelta

import pytest

from casefile.features.investigation.adapters.outbound.queue_memory import InMemoryJobQueue
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.worker import RunWorker

SECOND = timedelta(seconds=1)


class NoopExecutor:
    async def execute(self, lease: JobLease) -> None:
        return None


def worker(
    *,
    lease: timedelta = 60 * SECOND,
    timeout: timedelta = 30 * SECOND,
    retry: timedelta = 5 * SECOND,
) -> RunWorker:
    return RunWorker(
        InMemoryJobQueue([]),
        NoopExecutor(),
        lease_for=lease,
        execution_timeout=timeout,
        retry_delay=retry,
    )


@pytest.mark.parametrize(
    "timing",
    [
        pytest.param({"timeout": timedelta(0)}, id="zero-timeout"),
        pytest.param({"timeout": -SECOND}, id="negative-timeout"),
        pytest.param({"timeout": 60 * SECOND}, id="timeout-equal-to-lease"),
        pytest.param({"timeout": 61 * SECOND}, id="timeout-longer-than-lease"),
        pytest.param({"retry": -SECOND}, id="negative-retry-delay"),
        pytest.param({"timeout": 61 * SECOND, "retry": -SECOND}, id="both-wrong"),
    ],
)
def test_an_inconsistent_timing_is_refused(timing: dict[str, timedelta]) -> None:
    with pytest.raises(ValueError, match="Execution timeout must be positive and shorter"):
        worker(**timing)


@pytest.mark.parametrize(
    "timing",
    [
        pytest.param({}, id="defaults"),
        pytest.param({"retry": timedelta(0)}, id="immediate-retry"),
        pytest.param({"timeout": timedelta(microseconds=1)}, id="smallest-timeout"),
        pytest.param({"timeout": 60 * SECOND - timedelta(microseconds=1)}, id="just-under-lease"),
    ],
)
def test_a_consistent_timing_is_accepted(timing: dict[str, timedelta]) -> None:
    assert worker(**timing).lease_for == 60 * SECOND
