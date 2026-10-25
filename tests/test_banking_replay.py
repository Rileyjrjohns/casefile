from dataclasses import replace

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
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
from casefile.features.investigation.domain.run import RunState
from tests.contracts.support import FixedClock
from tests.test_agent_workflow import RUN, create_run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "number,kind",
    [
        (101, AlertKind.PAYMENT_FRAUD),
        (102, AlertKind.CREDIT_DOCUMENT_FRAUD),
        (103, AlertKind.SANCTIONS_IDENTITY_REVIEW),
    ],
)
async def test_banking_run_replays_without_live_tools_or_decisions(
    number: int, kind: AlertKind
) -> None:
    original = await create_run(tools=6, subject=f"demo-customer-{number}", kind=kind)
    worker = build_agent_worker(
        original,
        MemoryInvestigationTools(load_seed(banking=True)),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        risk_enabled=True,
    )
    await worker.run_once()
    recorded = await original.journal.load(RUN)
    assert recorded is not None and recorded.snapshot.run.state == RunState.AWAITING_REVIEW
    fresh = await create_run(tools=6, subject=f"demo-customer-{number}", kind=kind)
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
