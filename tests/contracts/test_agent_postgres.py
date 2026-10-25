from decimal import Decimal

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext
from casefile.bootstrap.decisions import DecisionBridge, demo_decisions
from casefile.features.case_intake.domain.case import AlertKind, Case
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import TypedEvidenceGateway
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.run import Run, RunState
from tests.contracts.support import AT, CASE, RUN, Backend, FixedClock, value
from tests.test_recommendation_policy import ClosingDecision

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("force_closure", [False, True])
async def test_postgres_worker_commits_complete_trace_and_survives_reconnection(
    pg_backend: Backend,
    force_closure: bool,
) -> None:
    assert pg_backend.journal is not None and pg_backend.engine is not None
    value(
        await pg_backend.cases.add(
            value(
                Case.create(
                    CASE,
                    "agent-alert",
                    "demo-customer-002",
                    AlertKind.UNUSUAL_TRANSACTION,
                    "Demo transfer",
                    AT,
                )
            )
        )
    )
    value(
        await pg_backend.runs.enqueue(
            Run.new(
                RUN,
                CASE,
                value(BudgetLimits.create(5, 100_000, Decimal("1"))),
            )
        )
    )
    agent = InvestigationAgent(
        pg_backend.journal,
        StoredInvestigationContext(pg_backend.cases),
        TypedEvidenceGateway(MemoryInvestigationTools(load_seed())),
        FakeLanguageModel(),
        AgentJsonCodec(),
        FixedClock(),
        MemoryTelemetry(),
        DecisionBridge(ClosingDecision()) if force_closure else demo_decisions(),
    )
    assert await RunWorker(pg_backend.queue, agent).run_once()
    before = await pg_backend.journal.load(RUN)
    assert before is not None and before.snapshot.run.state == RunState.AWAITING_REVIEW
    assert (
        len(
            [
                event
                for event in before.events
                if event.phase == "succeeded" and not event.operation.startswith("decision.")
            ]
        )
        == 6
    )
    assert any(event.operation.startswith("decision.") for event in before.events)
    assert sum(event.operation == "policy.recommendation.v1" for event in before.events) == int(
        force_closure
    )
    assert before.snapshot.run.analysis is not None
    assert before.snapshot.run.analysis.recommendation.value == "escalate"
    await pg_backend.engine.dispose()
    assert await pg_backend.journal.load(RUN) == before
