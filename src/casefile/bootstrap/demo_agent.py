"""Run one isolated demonstration investigation without requiring a database."""

import argparse
import asyncio
import json
import os
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx

from casefile.bootstrap.agent import build_agent_worker
from casefile.bootstrap.memory import build_memory_application
from casefile.bootstrap.observability import configured_telemetry
from casefile.bootstrap.tools import connect_demo_tools
from casefile.features.case_intake.application.open_case import OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.http_model import (
    HttpLanguageModel,
    ModelSettings,
)
from casefile.features.investigation.adapters.outbound.agent.journal_sql import TraceDocument
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.documents import encode_run
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent_types import ExecutionSnapshot, LanguageModel
from casefile.features.investigation.application.start_run import StartRunCommand
from casefile.features.investigation.application.tools import InvestigationTools
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Err, Ok


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def run_demo(*, customer: str, use_mcp: bool, use_http: bool) -> ExecutionSnapshot:
    clock = SystemClock()
    app = build_memory_application(clock)
    case_id, run_id = CaseId(uuid4()), RunId(uuid4())
    opened = await app.open_case.execute(
        OpenCaseCommand(
            case_id,
            "demo-agent-alert",
            customer,
            AlertKind.UNUSUAL_TRANSACTION,
            "Fictional demonstration alert",
        )
    )
    limits = BudgetLimits.create(5, 100_000, Decimal("1"))
    assert isinstance(opened, Ok) and isinstance(limits, Ok)
    started = await app.start_run.execute(StartRunCommand(run_id, case_id, limits.value))
    if isinstance(started, Err):
        raise RuntimeError(started.error.message)
    async with AsyncExitStack() as stack:
        telemetry = stack.enter_context(configured_telemetry(os.environ))
        tools: InvestigationTools = (
            await stack.enter_async_context(connect_demo_tools())
            if use_mcp
            else MemoryInvestigationTools(load_seed())
        )
        model: LanguageModel = FakeLanguageModel()
        if use_http:
            settings = ModelSettings.from_environment(os.environ)
            client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False))
            model = HttpLanguageModel(client, settings, AgentJsonCodec())
        await build_agent_worker(app, tools, model, clock, telemetry).run_once()
    snapshot = await app.journal.load(run_id)
    assert snapshot is not None
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Investigate fictional evidence and await human review."
    )
    parser.add_argument(
        "--customer",
        choices=[f"demo-customer-{i:03}" for i in range(1, 4)],
        default="demo-customer-002",
    )
    parser.add_argument(
        "--mcp", action="store_true", help="Launch the five real local MCP processes."
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use the explicitly configured external narrative model.",
    )
    args = parser.parse_args()
    snapshot = asyncio.run(run_demo(customer=args.customer, use_mcp=args.mcp, use_http=args.http))
    print(
        json.dumps(
            {
                "schema_version": 1,
                "run": encode_run(snapshot.snapshot.run),
                "events": [
                    TraceDocument.model_validate(event).model_dump(mode="json")
                    for event in snapshot.events
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
