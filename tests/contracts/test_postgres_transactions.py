import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from casefile.features.case_intake.domain.case import Case
from casefile.features.investigation.domain.errors import InvestigationErrorCode
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Err, Ok
from tests.contracts.support import (
    AT,
    CASE,
    REVIEW,
    RUN,
    Backend,
    case,
    pending,
    review,
    seed,
    value,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@asynccontextmanager
async def reject_inserts(engine: AsyncEngine, table: str) -> AsyncIterator[None]:
    assert table in {"run_jobs", "reviews", "run_events"}
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION casefile_reject_insert() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected storage failure'; END $$"
            )
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER reject_insert BEFORE INSERT ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION casefile_reject_insert()"
            )
        )
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP TRIGGER reject_insert ON {table}"))
            await connection.execute(text("DROP FUNCTION casefile_reject_insert()"))


async def test_failed_job_insert_rolls_back_the_run(pg_backend: Backend) -> None:
    assert pg_backend.engine is not None
    value(await pg_backend.cases.add(case()))
    async with reject_inserts(pg_backend.engine, "run_jobs"):
        with pytest.raises(DBAPIError, match="injected storage failure"):
            await pg_backend.runs.enqueue(pending())
    assert await pg_backend.runs.get(RUN) is None
    assert await pg_backend.queue.claim(lease_for=timedelta(seconds=30)) is None


async def test_failed_review_insert_undoes_the_closed_run(pg_backend: Backend) -> None:
    assert pg_backend.engine is not None
    await seed(pg_backend, ready_for_review=True)
    before = await pg_backend.runs.get(RUN)
    async with reject_inserts(pg_backend.engine, "reviews"):
        with pytest.raises(DBAPIError, match="injected storage failure"):
            await pg_backend.gateway.commit(review(), expected_revision=2)
    assert await pg_backend.runs.get(RUN) == before
    assert await pg_backend.reviews.get(REVIEW) is None


async def test_concurrent_starts_enforce_one_active_run(pg_backend: Backend) -> None:
    value(await pg_backend.cases.add(case()))
    second = Run.new(RunId(UUID(int=99)), CASE, pending().limits)
    outcomes = await asyncio.gather(
        pg_backend.runs.enqueue(pending()), pg_backend.runs.enqueue(second)
    )
    assert sum(isinstance(result, Ok) for result in outcomes) == 1
    assert sum(isinstance(result, Err) for result in outcomes) == 1
    assert await pg_backend.queue.claim(lease_for=timedelta(seconds=30)) is not None
    assert await pg_backend.queue.claim(lease_for=timedelta(seconds=30)) is None


async def test_foreign_key_failure_is_a_business_error(pg_backend: Backend) -> None:
    result = await pg_backend.runs.enqueue(pending())
    assert isinstance(result, Err) and result.error.code == InvestigationErrorCode.CASE_NOT_FOUND


async def test_skip_locked_does_not_block_on_another_worker(pg_backend: Backend) -> None:
    assert pg_backend.engine is not None
    await seed(pg_backend)
    async with pg_backend.engine.begin() as connection:
        await connection.execute(
            text("SELECT run_id FROM run_jobs WHERE run_id = :id FOR UPDATE"), {"id": RUN}
        )
        assert (
            await asyncio.wait_for(
                pg_backend.queue.claim(lease_for=timedelta(seconds=30)), timeout=2
            )
            is None
        )
    assert await pg_backend.queue.claim(lease_for=timedelta(seconds=30)) is not None


async def test_parallel_workers_claim_distinct_jobs(pg_backend: Backend) -> None:
    await seed(pg_backend)
    other_case = value(
        Case.create(CaseId(UUID(int=98)), "other-alert", "other-customer", case().kind, "Other", AT)
    )
    value(await pg_backend.cases.add(other_case))
    other_run = Run.new(RunId(UUID(int=99)), other_case.id, pending().limits)
    value(await pg_backend.runs.enqueue(other_run))
    claims = await asyncio.gather(
        pg_backend.queue.claim(lease_for=timedelta(seconds=30)),
        pg_backend.queue.claim(lease_for=timedelta(seconds=30)),
    )
    assert all(lease is not None for lease in claims)
    assert {lease.run_id for lease in claims if lease is not None} == {RUN, other_run.id}


async def test_expired_lease_is_reclaimed_and_stale_worker_is_fenced(pg_backend: Backend) -> None:
    assert pg_backend.engine is not None
    await seed(pg_backend)
    old = await pg_backend.queue.claim(lease_for=timedelta(seconds=30))
    assert old is not None
    async with pg_backend.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE run_jobs SET lease_until = clock_timestamp() - interval '1 second' "
                "WHERE run_id = :id"
            ),
            {"id": RUN},
        )
    assert not await pg_backend.queue.ack(old)
    fresh = await pg_backend.queue.claim(lease_for=timedelta(seconds=30))
    assert fresh is not None and fresh.attempt == 2 and fresh.token != old.token
    assert not await pg_backend.queue.retry(old, reason="Stale worker", delay=timedelta(0))
    assert await pg_backend.queue.ack(fresh)


async def test_reconnect_keeps_data_and_migrations_match_models(
    pg_backend: Backend, postgres_url: str
) -> None:
    assert pg_backend.engine is not None
    await seed(pg_backend, ready_for_review=True)
    before = await pg_backend.runs.get(RUN)
    await pg_backend.engine.dispose()
    assert await pg_backend.runs.get(RUN) == before
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    await asyncio.to_thread(command.check, config)


async def test_migration_downgrade_and_upgrade(pg_backend: Backend, postgres_url: str) -> None:
    assert pg_backend.engine is not None
    await pg_backend.engine.dispose()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")
    await seed(pg_backend)
    assert await pg_backend.runs.get(RUN) is not None
