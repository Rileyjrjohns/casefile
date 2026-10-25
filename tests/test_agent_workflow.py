"""A queued investigation stops at human review and can be replayed offline."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
from casefile.bootstrap.memory import MemoryApplication, build_memory_application
from casefile.features.case_intake.application.open_case import OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.decisions import ReplayDecisionGateway
from casefile.features.investigation.adapters.outbound.agent.models import (
    FakeLanguageModel,
    ReplayLanguageModel,
)
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import ReplayEvidenceGateway
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.start_run import StartRunCommand
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.run import RunState
from casefile.shared.ids import CaseId, RunId
from tests.contracts.support import FixedClock, value

CASE = CaseId(UUID(int=601))
RUN = RunId(UUID(int=602))


async def create_run(
    *,
    tools: int = 5,
    tokens: int = 100_000,
    subject: str = "demo-customer-002",
    kind: AlertKind = AlertKind.UNUSUAL_TRANSACTION,
) -> MemoryApplication:
    app = build_memory_application(FixedClock())
    value(
        await app.open_case.execute(
            OpenCaseCommand(
                CASE,
                "agent-alert",
                subject,
                kind,
                "Fictional transfer",
            )
        )
    )
    value(
        await app.start_run.execute(
            StartRunCommand(
                RUN,
                CASE,
                value(BudgetLimits.create(tools, tokens, Decimal("10"))),
            )
        )
    )
    return app


@pytest.mark.asyncio
async def test_worker_gathers_real_records_and_stops_before_human_decision() -> None:
    app = await create_run()
    model = FakeLanguageModel()
    telemetry = MemoryTelemetry()
    worker = build_agent_worker(
        app, MemoryInvestigationTools(load_seed()), model, FixedClock(), telemetry
    )
    assert await worker.run_once()
    snapshot = await app.journal.load(RUN)
    assert snapshot is not None
    run = snapshot.snapshot.run
    assert run.state == RunState.AWAITING_REVIEW
    assert run.human_decision is None
    assert run.analysis is not None and run.analysis.conclusions
    assert all(
        source in run.sources for item in run.analysis.conclusions for source in item.sources
    )
    assert run.usage.tool_calls == 5
    assert len(model.requests) == 1
    assert (
        sum(
            event.phase == "started" and not event.operation.startswith("decision.")
            for event in snapshot.events
        )
        == 6
    )
    assert sum(name.startswith("tool.") for name in telemetry.names) == 5
    assert "model.generate" in telemetry.names and "investigation.run" in telemetry.names
    assert not await worker.run_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("tools", "tokens"), [(0, 100_000), (4, 100_000), (5, 0)])
async def test_budget_prevents_the_next_external_call(tools: int, tokens: int) -> None:
    app = await create_run(tools=tools, tokens=tokens)
    model = FakeLanguageModel()
    worker = build_agent_worker(
        app, MemoryInvestigationTools(load_seed()), model, FixedClock(), MemoryTelemetry()
    )
    assert await worker.run_once()
    snapshot = await app.journal.load(RUN)
    assert snapshot is not None
    assert snapshot.snapshot.run.state == RunState.FAILED
    assert snapshot.snapshot.run.usage.tool_calls <= tools
    assert not model.requests


@pytest.mark.asyncio
async def test_successful_trace_replays_without_live_tools_or_model() -> None:
    original = await create_run()
    worker = build_agent_worker(
        original,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
    )
    await worker.run_once()
    recorded = await original.journal.load(RUN)
    assert recorded is not None
    fresh = await create_run()
    codec = AgentJsonCodec()
    agent = InvestigationAgent(
        fresh.journal,
        StoredInvestigationContext(fresh.cases),
        ReplayEvidenceGateway(recorded.events, codec),
        ReplayLanguageModel(recorded.events, codec),
        codec,
        FixedClock(),
        MemoryTelemetry(),
        ReplayDecisionGateway(recorded.events),
    )
    replay_worker = replace(worker, queue=fresh.queue, executor=agent)
    await replay_worker.run_once()
    replayed = await fresh.journal.load(RUN)
    assert replayed is not None
    assert replayed.snapshot.run == recorded.snapshot.run
