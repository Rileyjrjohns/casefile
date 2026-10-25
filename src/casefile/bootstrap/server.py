"""Run the local demonstration with optional PostgreSQL and real MCP evidence tools."""

import argparse
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace

import httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from casefile.bootstrap.agent import build_agent_worker
from casefile.bootstrap.api import create_app
from casefile.bootstrap.decisions import configured_decisions
from casefile.bootstrap.demo_agent import SystemClock
from casefile.bootstrap.mcp_server import build_server
from casefile.bootstrap.memory import MemoryApplication, build_memory_application
from casefile.bootstrap.observability import configured_telemetry
from casefile.bootstrap.postgres import PostgresApplication, build_postgres_application
from casefile.bootstrap.runtime import (
    DemoPromotionAccess,
    DemoReviewerAccess,
    Runtime,
    seed_runtime,
)
from casefile.bootstrap.tools import connect_demo_tools
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.http_model import (
    HttpLanguageModel,
    ModelSettings,
)
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent_types import LanguageModel
from casefile.features.investigation.application.tools import InvestigationTools
from casefile.features.review.application.record_review import RecordReview


@asynccontextmanager
async def runtime_context() -> AsyncIterator[Runtime]:
    clock = SystemClock()
    async with AsyncExitStack() as stack:
        app: MemoryApplication | PostgresApplication
        url = os.environ.get("CASEFILE_DATABASE_URL")
        if url:
            engine = create_async_engine(url, pool_pre_ping=True)
            stack.push_async_callback(engine.dispose)
            app = build_postgres_application(
                async_sessionmaker(engine, expire_on_commit=False),
                clock,
                DemoReviewerAccess(),
                DemoPromotionAccess(),
            )
        else:
            memory = build_memory_application(clock)
            app = replace(
                memory,
                record_review=RecordReview(memory.review_gateway, DemoReviewerAccess(), clock),
                promote_correction=replace(memory.promote_correction, access=DemoPromotionAccess()),
            )
        tool_mode = os.environ.get("CASEFILE_TOOLS", "memory")
        if tool_mode not in {"memory", "mcp"}:
            raise ValueError("CASEFILE_TOOLS must be memory or mcp.")
        tools: InvestigationTools = (
            await stack.enter_async_context(connect_demo_tools(banking=True))
            if tool_mode == "mcp"
            else MemoryInvestigationTools(load_seed(banking=True))
        )
        model: LanguageModel = FakeLanguageModel()
        model_mode = os.environ.get("CASEFILE_LANGUAGE_MODEL", "fake")
        if model_mode == "http":
            client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
            model = HttpLanguageModel(
                client, ModelSettings.from_environment(os.environ), AgentJsonCodec()
            )
        elif model_mode != "fake":
            raise ValueError("CASEFILE_LANGUAGE_MODEL must be fake or http.")
        telemetry = stack.enter_context(configured_telemetry(os.environ))
        decision_client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
        alternatives: dict[str, LanguageModel] = {}
        if os.environ.get("CASEFILE_FALLBACK_MODEL_URL"):
            fallback_env = {
                key.replace("CASEFILE_FALLBACK_MODEL_", "CASEFILE_MODEL_"): value
                for key, value in os.environ.items()
                if key.startswith("CASEFILE_FALLBACK_MODEL_")
            }
            alternatives["conservative"] = HttpLanguageModel(
                decision_client, ModelSettings.from_environment(fallback_env), AgentJsonCodec()
            )
        runtime = Runtime(
            app,
            build_agent_worker(
                app,
                tools,
                model,
                clock,
                telemetry,
                decisions=configured_decisions(os.environ, decision_client),
                risk_enabled=True,
                alternative_models=alternatives,
            ),
        )
        await seed_runtime(runtime, banking=True)
        yield runtime


def combined_app(runtime: Runtime) -> FastAPI:
    api = create_app(runtime, access_token=os.environ.get("CASEFILE_ACCESS_TOKEN"))
    mcp_server = build_server(runtime, background_worker=False)
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")
    original = api.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with original(app), mcp_app.router.lifespan_context(mcp_app):
            yield

    api.router.lifespan_context = lifespan
    api.mount("/mcp", mcp_app)
    return api


async def serve(host: str, port: int) -> None:
    async with runtime_context() as runtime:
        await uvicorn.Server(
            uvicorn.Config(combined_app(runtime), host=host, port=port, log_level="info")
        ).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the fictional Casefile review application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    asyncio.run(serve(args.host, args.port))


if __name__ == "__main__":
    main()
