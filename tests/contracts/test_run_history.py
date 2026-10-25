import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from casefile.features.investigation.adapters.outbound.documents import encode_run
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import CaseId, RunId
from tests.contracts.support import CASE, Backend, case, pending, value

pytestmark = pytest.mark.asyncio


async def test_run_history_follows_creation_not_uuid_or_later_updates(backend: Backend) -> None:
    value(await backend.cases.add(case()))
    first = Run.new(RunId(UUID(int=999)), CASE, pending().limits)
    value(await backend.runs.enqueue(first))
    first_failed = value(first.fail("First attempt failed."))
    value(await backend.runs.save(first_failed, expected_revision=1))
    second = Run.new(RunId(UUID(int=1)), CASE, pending().limits)
    value(await backend.runs.enqueue(second))
    value(await backend.runs.save(first_failed, expected_revision=2))
    assert [s.run.id for s in await backend.runs.for_case(CASE)] == [second.id, first.id]
    assert await backend.runs.for_case(CaseId(UUID(int=101))) == ()
    if backend.engine is not None:
        await backend.engine.dispose()
        assert [s.run.id for s in await backend.runs.for_case(CASE)] == [second.id, first.id]


@pytest.mark.postgres
async def test_upgrade_preserves_old_runs_and_assigns_new_runs_after_backfill(
    pg_backend: Backend,
    postgres_url: str,
) -> None:
    assert pg_backend.engine is not None
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    await asyncio.to_thread(command.downgrade, config, "0002")
    try:
        value(await pg_backend.cases.add(case()))
        first, second, third = (
            value(Run.new(RunId(UUID(int=i)), CASE, pending().limits).fail("Legacy failure."))
            for i in (999, 500, 1)
        )
        async with pg_backend.engine.begin() as connection:
            for run, day in ((first, 1), (second, 20), (third, 3)):
                await connection.execute(
                    text(
                        "INSERT INTO runs(id, case_id, state, revision, payload) "
                        "VALUES (:id, :case_id, 'FAILED', 1, CAST(:payload AS jsonb))"
                    ),
                    {"id": run.id, "case_id": CASE, "payload": json.dumps(encode_run(run))},
                )
                await connection.execute(
                    text(
                        "INSERT INTO run_jobs(run_id, state, available_at) "
                        "VALUES (:id, 'DONE', :at)"
                    ),
                    {"id": run.id, "at": datetime(2030, 1, day, tzinfo=UTC)},
                )
            # A later retry schedule must not displace the earlier recorded execution.
            await connection.execute(
                text(
                    "INSERT INTO run_events(run_id, sequence, payload) "
                    "VALUES (:id, 1, CAST(:payload AS jsonb))"
                ),
                {"id": second.id, "payload": json.dumps({"at": "2030-01-02T12:00:00Z"})},
            )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    history = await pg_backend.runs.for_case(CASE)
    assert [item.run for item in history] == [third, second, first]
    newest = Run.new(RunId(UUID(int=0)), CASE, pending().limits)
    value(await pg_backend.runs.enqueue(newest))
    assert [item.run.id for item in await pg_backend.runs.for_case(CASE)] == [
        newest.id,
        third.id,
        second.id,
        first.id,
    ]
    await asyncio.to_thread(command.check, config)
