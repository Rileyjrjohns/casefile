from dataclasses import replace
from decimal import Decimal

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
from casefile.bootstrap.decisions import DecisionBridge, demo_decisions
from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel, answer
from casefile.features.decisioning.domain.decisions import Answer, Question
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
from casefile.features.investigation.application.decisions import DecisionRequest
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.run import RunState
from tests.contracts.support import FixedClock, value
from tests.test_agent_workflow import RUN, create_run


@pytest.mark.asyncio
async def test_all_decision_purposes_are_journaled_and_risk_has_an_observed_source() -> None:
    app = await create_run()
    worker = build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        decisions=demo_decisions(),
        risk_enabled=True,
    )
    await worker.run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.AWAITING_REVIEW
    purposes = {
        e.operation.split(".")[1] for e in current.events if e.operation.startswith("decision.")
    }
    assert purposes == {
        "priority",
        "alert_type",
        "tool_route",
        "model_route",
        "guard",
        "injection",
        "signal",
        "recommendation",
        "risk",
    }
    run = current.snapshot.run
    assert any(s.tool == "risk_model@risk-v1" for s in run.sources)
    assert run.analysis is not None
    assert any(s.tool == "risk_model@risk-v1" for c in run.analysis.conclusions for s in c.sources)
    assert all(e.reserved is not None for e in current.events if e.phase == "started")


class DenyingDecision(FakeDecisionModel):
    async def probability(self, question: Question) -> Answer:
        if question.purpose == "guard":
            return answer(question, 0.0)
        return await super().probability(question)


@pytest.mark.asyncio
async def test_guard_denial_prevents_tool_call() -> None:
    app = await create_run()
    worker = build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        decisions=DecisionBridge(DenyingDecision()),
    )
    await worker.run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.FAILED
    assert current.snapshot.run.usage.tool_calls == 0


class ExpensiveDecision(DecisionBridge):
    purpose: str = ""

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        return value(
            BudgetUsage.create(0, 100001 if request.purpose == self.purpose else 0, Decimal(0))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "purpose",
    [
        "priority",
        "alert_type",
        "tool_route",
        "guard",
        "injection",
        "signal",
        "risk",
        "recommendation",
        "model_route",
    ],
)
async def test_every_decision_reserves_before_invoking_a_provider(purpose: str) -> None:
    app = await create_run()

    class Expensive(ExpensiveDecision):
        pass

    Expensive.purpose = purpose
    model = FakeLanguageModel()
    await build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        model,
        FixedClock(),
        MemoryTelemetry(),
        decisions=Expensive(routes=demo_decisions().routes),
        risk_enabled=True,
    ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.FAILED
    assert current.snapshot.run.usage.tokens == 0
    assert not model.requests


@pytest.mark.asyncio
async def test_complete_risk_enabled_trace_replays_exactly() -> None:
    original = await create_run()
    worker = build_agent_worker(
        original,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        risk_enabled=True,
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
        risk_enabled=True,
    )
    await replace(worker, queue=fresh.queue, executor=agent).run_once()
    replayed = await fresh.journal.load(RUN)
    assert replayed == recorded


@pytest.mark.asyncio
async def test_model_route_selects_the_configured_narrative_adapter() -> None:
    class Conservative(FakeDecisionModel):
        async def choose(self, question: Question) -> Answer:
            if question.purpose == "model_route":
                return answer(question, "conservative")
            return await super().choose(question)

    app = await create_run()
    primary, alternative = FakeLanguageModel(), FakeLanguageModel()
    await build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        primary,
        FixedClock(),
        MemoryTelemetry(),
        decisions=DecisionBridge(Conservative()),
        alternative_models={"conservative": alternative},
    ).run_once()
    assert not primary.requests
    assert len(alternative.requests) == 1
    assert alternative.requests[0].max_output_tokens == 512
