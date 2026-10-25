from uuid import UUID

import pytest

from casefile.features.investigation.adapters.outbound.memory import (
    InMemoryCaseLookup,
    InMemoryRunStore,
)
from casefile.features.investigation.application.start_run import StartRun, StartRunCommand
from casefile.features.investigation.domain.errors import InvestigationErrorCode
from casefile.features.investigation.domain.run import RunState
from casefile.features.investigation.tests.factories import CASE_ID, RUN_ID, run_at, value
from casefile.shared.ids import RunId
from casefile.shared.result import Err, Ok


def command(run_id: RunId = RUN_ID) -> StartRunCommand:
    return StartRunCommand(run_id, CASE_ID, run_at(RunState.PENDING).limits)


@pytest.mark.asyncio
async def test_start_run_persists_pending_run_and_exactly_one_job() -> None:
    store = InMemoryRunStore()
    result = await StartRun(InMemoryCaseLookup({CASE_ID}), store).execute(command())
    assert isinstance(result, Ok)
    assert result.value.state == RunState.PENDING
    assert store.jobs == [RUN_ID]
    snapshot = await store.get(RUN_ID)
    assert snapshot is not None and snapshot.run == result.value and snapshot.revision == 1


@pytest.mark.asyncio
async def test_missing_case_has_no_run_or_job_side_effects() -> None:
    store = InMemoryRunStore()
    result = await StartRun(InMemoryCaseLookup(), store).execute(command())
    assert isinstance(result, Err) and result.error.code == InvestigationErrorCode.CASE_NOT_FOUND
    assert store.runs == {} and store.jobs == []


@pytest.mark.asyncio
async def test_duplicate_run_and_active_case_do_not_queue_another_job() -> None:
    store = InMemoryRunStore()
    use_case = StartRun(InMemoryCaseLookup({CASE_ID}), store)
    assert isinstance(await use_case.execute(command()), Ok)
    duplicate = await use_case.execute(command())
    assert (
        isinstance(duplicate, Err)
        and duplicate.error.code == InvestigationErrorCode.RUN_ALREADY_EXISTS
    )
    other = await use_case.execute(command(RunId(UUID(int=99))))
    assert isinstance(other, Err) and other.error.code == InvestigationErrorCode.ACTIVE_RUN_EXISTS
    assert store.jobs == [RUN_ID] and len(store.runs) == 1


@pytest.mark.asyncio
async def test_technical_enqueue_failure_creates_neither_run_nor_job() -> None:
    store = InMemoryRunStore()
    store.fail_writes = True
    with pytest.raises(RuntimeError, match="storage"):
        await StartRun(InMemoryCaseLookup({CASE_ID}), store).execute(command())
    assert store.runs == {} and store.jobs == []


@pytest.mark.asyncio
async def test_run_updates_compare_revisions_and_allow_a_new_run_after_failure() -> None:
    store = InMemoryRunStore()
    use_case = StartRun(InMemoryCaseLookup({CASE_ID}), store)
    initial = await use_case.execute(command())
    assert isinstance(initial, Ok)
    failed = value(initial.value.fail("Tool unavailable"))
    assert isinstance(await store.save(failed, expected_revision=1), Ok)
    assert isinstance(await store.save(initial.value, expected_revision=1), Err)
    assert isinstance(await use_case.execute(command(RunId(UUID(int=99)))), Ok)
    assert len(store.jobs) == 2


@pytest.mark.asyncio
async def test_failed_run_cannot_be_reactivated_from_an_old_snapshot() -> None:
    store = InMemoryRunStore()
    original = run_at(RunState.PENDING)
    value(await store.enqueue(original))
    value(await store.save(value(original.fail("Unavailable")), expected_revision=1))
    result = await store.save(original, expected_revision=2)
    assert isinstance(result, Err)
    snapshot = await store.get(RUN_ID)
    assert snapshot is not None and snapshot.run.state == RunState.FAILED
