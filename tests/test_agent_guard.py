"""The pre-tool guard decides from verifiable facts and can stop a real run."""

import json
from dataclasses import replace

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
from casefile.bootstrap.decisions import demo_decisions
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import TypedEvidenceGateway
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.agent_types import EvidenceBatch
from casefile.features.investigation.application.decisions import (
    DecisionGateway,
    DecisionRequest,
    DecisionResponse,
)
from casefile.features.investigation.application.tools import (
    InvalidToolQuery,
    KycProfile,
    ToolName,
)
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.run import RunState
from casefile.shared.result import Err, Ok, Result
from tests.contracts.support import FixedClock
from tests.test_agent_workflow import RUN, create_run

GUARD_FACTS = {
    "tool",
    "query",
    "subject_id",
    "query_scope",
    "tool_allowed",
    "already_called",
}


class RecordingDecisions:
    """Delegates to the demo policy while keeping every request the agent sent."""

    def __init__(self) -> None:
        self.inner: DecisionGateway = demo_decisions()
        self.requests: list[DecisionRequest] = []

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        return self.inner.quote(request)

    async def execute(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return await self.inner.execute(request)


class ForeignProfileEvidence(TypedEvidenceGateway):
    """A KYC tool that answers with a profile of a different customer."""

    def __init__(self) -> None:
        super().__init__(MemoryInvestigationTools(load_seed()))
        self.calls: list[ToolName] = []

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        self.calls.append(tool)
        result = await super().fetch(tool, query)
        if isinstance(result, Err) or tool != ToolName.KYC:
            return result
        foreign = tuple(
            replace(record, customer_id="demo-customer-009")
            if isinstance(record, KycProfile)
            else record
            for record in result.value.records
        )
        return Ok(replace(result.value, records=foreign))


def keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in keys(item)}
    return set()


@pytest.mark.asyncio
async def test_guard_denies_a_sanctions_query_built_from_another_customers_profile() -> None:
    app = await create_run()
    gateway = ForeignProfileEvidence()
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        gateway,
        FakeLanguageModel(),
        AgentJsonCodec(),
        FixedClock(),
        MemoryTelemetry(),
        RecordingDecisions(),
    )
    await RunWorker(app.queue, agent).run_once()
    current = await app.journal.load(RUN)
    assert current is not None
    run = current.snapshot.run
    assert run.state == RunState.FAILED
    assert run.failure_reason is not None and "unrelated query scope" in run.failure_reason
    assert ToolName.SANCTIONS not in gateway.calls
    assert run.usage.tool_calls == 4
    assert run.analysis is None


@pytest.mark.asyncio
async def test_no_decision_state_sent_by_the_agent_carries_an_authorization_flag() -> None:
    app = await create_run()
    decisions = RecordingDecisions()
    worker = build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        decisions=decisions,
        risk_enabled=True,
    )
    await worker.run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.AWAITING_REVIEW
    assert decisions.requests
    assert all("authorized" not in keys(json.loads(r.state_json)) for r in decisions.requests)
    guards = [r for r in decisions.requests if r.purpose == "guard"]
    assert {r.key for r in guards} == {t.value for t in ToolName} - {ToolName.DOCUMENTS}
    assert all(set(json.loads(r.state_json)) == GUARD_FACTS for r in guards)
    assert all(json.loads(r.state_json)["already_called"] is False for r in guards)
