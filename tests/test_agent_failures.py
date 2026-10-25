import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
from casefile.bootstrap.decisions import demo_decisions
from casefile.bootstrap.tools import connect_demo_tools
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.decisions import ReplayDecisionGateway
from casefile.features.investigation.adapters.outbound.agent.models import (
    FakeLanguageModel,
    ReplayLanguageModel,
)
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import (
    ReplayEvidenceGateway,
    TypedEvidenceGateway,
)
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.agent_types import (
    EvidenceBatch,
    ModelRequest,
    ModelResponse,
    RecordedCallFailure,
    SourceLink,
)
from casefile.features.investigation.application.tools import InvalidToolQuery, ToolName
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.run import RunState
from casefile.shared.result import Result
from tests.contracts.support import FixedClock
from tests.test_agent_workflow import RUN, create_run


class AlteredModel(FakeLanguageModel):
    def __init__(self, defect: str) -> None:
        super().__init__()
        self.defect = defect

    async def generate(self, request: ModelRequest) -> ModelResponse:
        result = await super().generate(request)
        item = result.statements[0]
        if self.defect == "source":
            return replace(
                result,
                statements=(
                    replace(item, sources=(SourceLink("invented", "record-999"),)),
                    *result.statements[1:],
                ),
            )
        if self.defect == "unrelated":
            return replace(
                result,
                statements=(
                    replace(item, sources=(SourceLink(ToolName.NOTES, "note-002"),)),
                    *result.statements[1:],
                ),
            )
        if self.defect == "duplicate":
            return replace(result, statements=(*result.statements, item))
        if self.defect == "missing":
            return replace(result, statements=())
        if self.defect == "blank":
            return replace(result, summary=" ")
        if self.defect == "cost":
            return replace(result, estimated_cost=Decimal("11"))
        if self.defect == "tokens":
            return replace(result, input_tokens=100_001)
        return replace(result, output_tokens=request.max_output_tokens + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["source", "unrelated", "duplicate", "missing", "blank", "cost", "tokens", "output-cap"],
)
async def test_invalid_analysis_or_model_overrun_cannot_reach_review(defect: str) -> None:
    app = await create_run()
    model = AlteredModel(defect)
    await build_agent_worker(
        app, MemoryInvestigationTools(load_seed()), model, FixedClock(), MemoryTelemetry()
    ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.FAILED
    assert current.snapshot.run.analysis is None and current.snapshot.run.human_decision is None
    assert current.snapshot.run.failure_reason
    assert any(
        event.operation == "model.generate" and event.output_json for event in current.events
    )


class FlakyEvidence(TypedEvidenceGateway):
    def __init__(self, *, cancel: bool = False) -> None:
        super().__init__(MemoryInvestigationTools(load_seed()))
        self.failed = False
        self.cancel = cancel
        self.calls: list[ToolName] = []

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        self.calls.append(tool)
        if tool == ToolName.TRANSACTIONS and not self.failed:
            self.failed = True
            if self.cancel:
                raise asyncio.CancelledError()
            raise ConnectionError("Simulated transport failure")
        return await super().fetch(tool, query)


@pytest.mark.asyncio
async def test_known_tool_failure_retries_without_repeating_successful_calls() -> None:
    app = await create_run(tools=6)
    gateway = FlakyEvidence()
    model = FakeLanguageModel()
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        gateway,
        model,
        AgentJsonCodec(),
        FixedClock(),
        MemoryTelemetry(),
        demo_decisions(),
    )
    worker = RunWorker(app.queue, agent, retry_delay=timedelta(0))
    with pytest.raises(ConnectionError):
        await worker.run_once()
    assert await worker.run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.AWAITING_REVIEW
    assert current.snapshot.run.usage.tool_calls == 6
    assert gateway.calls.count(ToolName.KYC) == 1
    assert gateway.calls.count(ToolName.TRANSACTIONS) == 2
    assert len(model.requests) == 1
    fresh = await create_run(tools=6)
    codec = AgentJsonCodec()
    replay = InvestigationAgent(
        fresh.journal,
        StoredInvestigationContext(fresh.cases),
        ReplayEvidenceGateway(current.events, codec),
        ReplayLanguageModel(current.events, codec),
        codec,
        FixedClock(),
        MemoryTelemetry(),
        ReplayDecisionGateway(current.events),
    )
    replay_worker = RunWorker(fresh.queue, replay, retry_delay=timedelta(0))
    with pytest.raises(RecordedCallFailure):
        await replay_worker.run_once()
    await replay_worker.run_once()
    replayed = await fresh.journal.load(RUN)
    assert replayed is not None
    assert replayed.snapshot.run == current.snapshot.run
    assert replayed.events == current.events


@pytest.mark.asyncio
async def test_unknown_interrupted_outcome_fails_without_repeating_the_call() -> None:
    app = await create_run()
    gateway = FlakyEvidence(cancel=True)
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        gateway,
        FakeLanguageModel(),
        AgentJsonCodec(),
        FixedClock(),
        MemoryTelemetry(),
        demo_decisions(),
    )
    lease = await app.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None
    with pytest.raises(asyncio.CancelledError):
        await agent.execute(lease)
    assert await app.queue.retry(lease, reason="Simulated restart", delay=timedelta(0))
    await RunWorker(app.queue, agent).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.FAILED
    assert current.snapshot.run.usage.tool_calls == 2
    assert gateway.calls == [ToolName.KYC, ToolName.TRANSACTIONS]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject", "recommendation"),
    [
        ("demo-customer-001", "close"),
        ("demo-customer-003", "escalate"),
        ("demo-customer-999", "request_information"),
    ],
)
async def test_demo_policy_is_independent_of_model_decisions(
    subject: str, recommendation: str
) -> None:
    app = await create_run(subject=subject)
    await build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed()),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
    ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.analysis is not None
    assert current.snapshot.run.analysis.recommendation.value == recommendation
    assert current.snapshot.run.state == RunState.AWAITING_REVIEW


@pytest.mark.mcp
@pytest.mark.asyncio
async def test_agent_runs_against_all_five_mcp_subprocesses() -> None:
    app = await create_run()
    async with connect_demo_tools() as tools:
        await build_agent_worker(
            app, tools, FakeLanguageModel(), FixedClock(), MemoryTelemetry()
        ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.AWAITING_REVIEW
    assert current.snapshot.run.usage.tool_calls == 5


@pytest.mark.asyncio
async def test_invalid_case_subject_fails_with_a_recorded_query_error() -> None:
    app = await create_run(subject="not-a-demo-id")
    model = FakeLanguageModel()
    await build_agent_worker(
        app, MemoryInvestigationTools(load_seed()), model, FixedClock(), MemoryTelemetry()
    ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.state == RunState.FAILED
    assert not model.requests
    assert current.events[-1].error and current.events[-1].error.startswith("query:")


@pytest.mark.asyncio
async def test_failed_reservation_write_prevents_external_work() -> None:
    app = await create_run()
    app.journal.fail_writes = True
    model = FakeLanguageModel()
    with pytest.raises(RuntimeError, match="storage failure"):
        await build_agent_worker(
            app, MemoryInvestigationTools(load_seed()), model, FixedClock(), MemoryTelemetry()
        ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and not current.events
    assert current.snapshot.run.usage.tool_calls == 0
    assert not model.requests
