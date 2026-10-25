from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from casefile.features.investigation.application.agent_types import TraceEvent
from casefile.shared.result import Err
from tests.contracts.support import AT, RUN, Backend, seed, value
from tests.contracts.test_postgres_transactions import reject_inserts

pytestmark = pytest.mark.asyncio


async def test_checkpoint_is_atomic_and_requires_active_lease(backend: Backend) -> None:
    assert backend.journal is not None
    await seed(backend)
    lease = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None
    before = await backend.journal.load(RUN)
    assert before is not None and not before.events
    run = value(before.snapshot.run.start_gathering())
    event = TraceEvent(1, "case_context", "transition", AT)
    stale = replace(lease, token=uuid4())
    assert isinstance(await backend.journal.checkpoint(stale, run, event, expected_revision=1), Err)
    assert await backend.journal.load(RUN) == before
    after = value(await backend.journal.checkpoint(lease, run, event, expected_revision=1))
    assert after.snapshot.revision == 2 and after.events == (event,)
    assert isinstance(await backend.journal.checkpoint(lease, run, event, expected_revision=1), Err)
    assert await backend.queue.ack(lease)
    assert isinstance(
        await backend.journal.checkpoint(
            lease,
            run,
            replace(event, sequence=2),
            expected_revision=2,
        ),
        Err,
    )
    assert await backend.journal.load(RUN) == after


@pytest.mark.postgres
async def test_trace_insert_failure_rolls_back_run_and_budget(pg_backend: Backend) -> None:
    assert pg_backend.journal is not None and pg_backend.engine is not None
    await seed(pg_backend)
    lease = await pg_backend.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None
    before = await pg_backend.journal.load(RUN)
    assert before is not None
    async with reject_inserts(pg_backend.engine, "run_events"):
        with pytest.raises(DBAPIError):
            await pg_backend.journal.checkpoint(
                lease,
                value(before.snapshot.run.start_gathering()),
                TraceEvent(1, "case_context", "transition", AT),
                expected_revision=1,
            )
    assert await pg_backend.journal.load(RUN) == before
