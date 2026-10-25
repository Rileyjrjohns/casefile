from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from casefile.bootstrap.memory import build_memory_application
from casefile.bootstrap.postgres import build_postgres_application
from casefile.features.evaluation.adapters.outbound.memory import InMemoryPromotionAccess
from casefile.features.review.adapters.outbound.memory import InMemoryReviewerAccess
from tests.contracts.support import Backend, FixedClock

POSTGRES_IMAGE = (
    "postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2"
)
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    # Only a fresh container created here is ever migrated or truncated.
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="casefile",
        password="casefile-test-only",
        dbname="casefile_test",
        driver="psycopg",
    ) as container:
        url = container.get_connection_url()
        config = Config(str(PROJECT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(config, "head")
        yield url


@pytest_asyncio.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    if request.param == "memory":
        app = build_memory_application(FixedClock())
        yield Backend(
            app.cases,
            app.runs,
            app.start_run.cases,
            app.review_gateway,
            app.reviews,
            app.correction_lookup,
            app.references,
            app.queue,
            journal=app.journal,
        )
        return
    url = cast(str, request.getfixturevalue("postgres_url"))
    async with database_backend(url) as result:
        yield result


@pytest_asyncio.fixture
async def pg_backend(postgres_url: str) -> AsyncIterator[Backend]:
    async with database_backend(postgres_url) as result:
        yield result


@asynccontextmanager
async def database_backend(url: str) -> AsyncIterator[Backend]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE reference_examples, reviews, run_jobs, runs, cases CASCADE")
            )
        sql_app = build_postgres_application(
            async_sessionmaker(engine, expire_on_commit=False),
            FixedClock(),
            InMemoryReviewerAccess(),
            InMemoryPromotionAccess(),
        )
        yield Backend(
            sql_app.cases,
            sql_app.runs,
            sql_app.start_run.cases,
            sql_app.review_gateway,
            sql_app.reviews,
            sql_app.correction_lookup,
            sql_app.references,
            sql_app.queue,
            engine,
            sql_app.journal,
        )
    finally:
        await engine.dispose()
