"""Execution contracts: typed decision validation, guard threshold, evidence identity, replay."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import timedelta

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext
from casefile.bootstrap.decisions import demo_decisions
from casefile.bootstrap.memory import MemoryApplication
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import TypedEvidenceGateway
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent import InvestigationAgent, _Execution
from casefile.features.investigation.application.agent_types import (
    EvidenceBatch,
    EvidenceGateway,
    ExecutionSnapshot,
    InvestigationContext,
    LanguageModel,
    Signal,
    SourceLink,
    TraceEvent,
)
from casefile.features.investigation.application.decisions import (
    DecisionGateway,
    DecisionRequest,
    DecisionResponse,
    state_json,
)
from casefile.features.investigation.application.findings_policy import CURRENT_FINDINGS
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.tools import InvalidToolQuery, ToolName
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.result import Ok, Result
from tests.contracts.support import FixedClock, value
from tests.test_agent_workflow import RUN, create_run

type Override = Callable[[DecisionRequest, DecisionResponse], DecisionResponse]

INVALID = "Decision provider returned an invalid typed answer."


class OverridingDecisions:
    """The demonstration decision policy with selected answers replaced by the test."""

    def __init__(self, override: Override | None = None) -> None:
        self.inner = demo_decisions()
        self.override = override or (lambda _, response: response)
        self.requests: list[DecisionRequest] = []

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        return self.inner.quote(request)

    async def execute(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return self.override(request, await self.inner.execute(request))


def agent_for(
    app: MemoryApplication,
    decisions: DecisionGateway,
    tools: EvidenceGateway | None = None,
    *,
    model: LanguageModel | None = None,
    contexts: InvestigationContext | None = None,
    risk_enabled: bool = False,
    alternative_models: Mapping[str, LanguageModel] | None = None,
    findings_policy: str = CURRENT_FINDINGS,
) -> InvestigationAgent:
    return InvestigationAgent(
        app.journal,
        contexts or StoredInvestigationContext(app.cases),
        tools or TypedEvidenceGateway(MemoryInvestigationTools(load_seed())),
        model or FakeLanguageModel(),
        AgentJsonCodec(),
        FixedClock(),
        MemoryTelemetry(),
        decisions,
        risk_enabled=risk_enabled,
        alternative_models=alternative_models or {},
        findings_policy=findings_policy,
    )


async def final_run(app: MemoryApplication, decisions: DecisionGateway) -> Run:
    await RunWorker(app.queue, agent_for(app, decisions)).run_once()
    current = await app.journal.load(RUN)
    assert current is not None
    return current.snapshot.run


def answer(purpose: str, change: Callable[[DecisionResponse], DecisionResponse]) -> Override:
    return lambda request, response: change(response) if request.purpose == purpose else response


INVALID_ANSWERS = [
    pytest.param("injection", lambda r: replace(r, confidence=1.1), id="confidence-above-one"),
    pytest.param("injection", lambda r: replace(r, confidence=-0.1), id="confidence-below-zero"),
    pytest.param("injection", lambda r: replace(r, value=1.1), id="probability-above-one"),
    pytest.param("injection", lambda r: replace(r, value=-0.1), id="probability-below-zero"),
    pytest.param("injection", lambda r: replace(r, value="0.5"), id="probability-as-text"),
    pytest.param("priority", lambda r: replace(r, value="urgent"), id="choice-outside-options"),
    pytest.param("priority", lambda r: replace(r, value=1.0), id="choice-as-number"),
    pytest.param("signal", lambda r: replace(r, value=1.5), id="score-above-last-grade"),
    pytest.param("signal", lambda r: replace(r, value=2.0), id="score-at-option-count"),
    pytest.param("signal", lambda r: replace(r, value=-0.5), id="score-below-zero"),
    pytest.param("signal", lambda r: replace(r, value="fully supported"), id="score-as-text"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("purpose", "change"), INVALID_ANSWERS)
async def test_an_invalid_typed_answer_fails_the_run_with_that_reason(
    purpose: str, change: Callable[[DecisionResponse], DecisionResponse]
) -> None:
    app = await create_run()
    run = await final_run(app, OverridingDecisions(answer(purpose, change)))
    assert run.state == RunState.FAILED
    assert run.failure_reason == INVALID
    assert run.analysis is None


VALID_EXTREMES = [
    pytest.param("injection", lambda r: replace(r, confidence=0.0), id="confidence-zero"),
    pytest.param("injection", lambda r: replace(r, confidence=1.0), id="confidence-one"),
    pytest.param("injection", lambda r: replace(r, value=0.0), id="probability-zero"),
    pytest.param("injection", lambda r: replace(r, value=1.0), id="probability-one"),
    pytest.param("signal", lambda r: replace(r, value=0.0), id="score-zero"),
    pytest.param("signal", lambda r: replace(r, value=1.0), id="score-last-grade"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("purpose", "change"), VALID_EXTREMES)
async def test_typed_answers_at_the_ends_of_their_ranges_are_accepted(
    purpose: str, change: Callable[[DecisionResponse], DecisionResponse]
) -> None:
    app = await create_run()
    run = await final_run(app, OverridingDecisions(answer(purpose, change)))
    assert run.failure_reason is None
    assert run.state == RunState.AWAITING_REVIEW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "allowed"), [(0.8, True), (0.81, True), (0.79, False), (0.0, False)]
)
async def test_the_pre_tool_guard_allows_exactly_a_verdict_of_eighty_percent(
    verdict: float, allowed: bool
) -> None:
    app = await create_run()
    run = await final_run(
        app, OverridingDecisions(answer("guard", lambda r: replace(r, value=verdict)))
    )
    assert (run.state == RunState.AWAITING_REVIEW) is allowed
    if not allowed:
        assert run.state == RunState.FAILED
        assert run.failure_reason == (
            "Pre-tool decision guard denied the query: provider verdict below threshold."
        )
        assert run.usage.tool_calls == 0


class MismatchedEvidence(TypedEvidenceGateway):
    def __init__(self, change: Callable[[EvidenceBatch], EvidenceBatch]) -> None:
        super().__init__(MemoryInvestigationTools(load_seed()))
        self.change = change

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        result = await super().fetch(tool, query)
        return replace_batch(result, self.change)


def replace_batch(
    result: Result[EvidenceBatch, InvalidToolQuery],
    change: Callable[[EvidenceBatch], EvidenceBatch],
) -> Result[EvidenceBatch, InvalidToolQuery]:
    return Ok(change(result.value)) if isinstance(result, Ok) else result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda b: replace(b, tool=ToolName.NOTES), id="other-tool"),
        pytest.param(lambda b: replace(b, query="demo-customer-999"), id="other-query"),
    ],
)
async def test_evidence_for_another_tool_or_query_is_a_technical_failure(
    change: Callable[[EvidenceBatch], EvidenceBatch],
) -> None:
    app = await create_run()
    agent = agent_for(app, demo_decisions(), MismatchedEvidence(change))
    with pytest.raises(ValueError, match="does not match the requested tool and query"):
        await RunWorker(app.queue, agent).run_once()
    current = await app.journal.load(RUN)
    assert current is not None
    assert current.events[-1].phase == "failed"
    assert current.events[-1].error == "ValueError"
    assert current.snapshot.run.usage.tool_calls == 1


async def execution(app: MemoryApplication, *events: TraceEvent) -> _Execution:
    lease = await app.queue.claim(lease_for=timedelta(seconds=60))
    assert isinstance(lease, JobLease)
    loaded = await app.journal.load(RUN)
    assert loaded is not None
    current: ExecutionSnapshot = loaded
    for event in events:
        current = value(
            await app.journal.checkpoint(
                lease,
                current.snapshot.run,
                event,
                expected_revision=current.snapshot.revision,
            )
        )
    return _Execution(agent_for(app, demo_decisions()), lease, current)


def event(output: str | None, input_json: str = "{}") -> TraceEvent:
    return TraceEvent(
        1, "tool.demo", "succeeded", FixedClock().now(), input_json=input_json, output_json=output
    )


async def never_called() -> Result[str, InvalidToolQuery]:
    raise AssertionError("A stored successful call must not be repeated.")


@pytest.mark.asyncio
async def test_a_stored_successful_call_is_replayed_without_invoking_it_again() -> None:
    app = await create_run()
    running = await execution(app, event('{"stored":true}'))
    output = await running.perform(
        "tool.demo", "{}", BudgetUsage.zero(), never_called, lambda _: BudgetUsage.zero()
    )
    assert output == '{"stored":true}'
    assert running.run.state == RunState.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "requested"),
    [
        pytest.param(event('{"stored":true}'), '{"other":1}', id="input-changed"),
        pytest.param(event(None), "{}", id="output-missing"),
        pytest.param(event(None, '{"other":1}'), "{}", id="both"),
    ],
)
async def test_a_stored_call_that_no_longer_matches_the_plan_fails_the_run(
    stored: TraceEvent, requested: str
) -> None:
    app = await create_run()
    running = await execution(app, stored)
    output = await running.perform(
        "tool.demo", requested, BudgetUsage.zero(), never_called, lambda _: BudgetUsage.zero()
    )
    assert output is None
    assert running.run.state == RunState.FAILED
    assert (
        running.run.failure_reason == "Stored execution request no longer matches the current plan."
    )


CONCERN = Signal(
    "finding:amount", "Amounts differ.", 1.0, (SourceLink(ToolName.DOCUMENTS, "doc-1"),)
)
GAP = Signal(
    "gap:documents", "Documents are missing.", 1.0, (SourceLink(ToolName.DOCUMENTS, "doc-1"),)
)
CONSTRAINT = "policy.recommendation.v1"


def constraint_events(running: _Execution) -> list[TraceEvent]:
    return [item for item in running.current.events if item.operation == CONSTRAINT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposed", "signals", "has_profile", "gaps"),
    [
        (Recommendation.ESCALATE, (CONCERN,), True, False),
        (Recommendation.REQUEST_INFORMATION, (), False, True),
        (Recommendation.CLOSE, (), True, False),
    ],
)
async def test_an_unconstrained_recommendation_is_returned_without_a_record(
    proposed: Recommendation, signals: tuple[Signal, ...], has_profile: bool, gaps: bool
) -> None:
    running = await execution(await create_run())
    result = await running.constrain_recommendation(
        proposed, signals, has_profile=has_profile, evidence_gaps=gaps
    )
    assert result == proposed
    assert constraint_events(running) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signals", "has_profile", "gaps", "expected", "reasons"),
    [
        ((CONCERN,), True, False, Recommendation.ESCALATE, ["unresolved_concerns"]),
        ((), False, False, Recommendation.REQUEST_INFORMATION, ["missing_kyc"]),
        ((), True, True, Recommendation.REQUEST_INFORMATION, ["evidence_gaps"]),
        ((GAP,), True, False, Recommendation.REQUEST_INFORMATION, ["evidence_gaps"]),
    ],
)
async def test_a_constrained_closure_is_recorded_with_its_inputs_and_reasons(
    signals: tuple[Signal, ...],
    has_profile: bool,
    gaps: bool,
    expected: Recommendation,
    reasons: list[str],
) -> None:
    running = await execution(await create_run())
    result = await running.constrain_recommendation(
        Recommendation.CLOSE, signals, has_profile=has_profile, evidence_gaps=gaps
    )
    assert result == expected
    (recorded,) = constraint_events(running)
    assert recorded.phase == "transition"
    assert recorded.output_json == state_json(
        {"recommendation": expected.value, "reasons": reasons}
    )
    assert recorded.input_json == state_json(
        {
            "proposed": "close",
            "blocking_signals": [s.key for s in signals if s.key.startswith("finding:")],
            "has_profile": has_profile,
            "evidence_gaps": gaps,
        }
    )
    again = await running.constrain_recommendation(
        Recommendation.CLOSE, signals, has_profile=has_profile, evidence_gaps=gaps
    )
    assert again == expected
    assert len(constraint_events(running)) == 1
    assert running.run.state != RunState.FAILED


@pytest.mark.asyncio
async def test_a_stored_constraint_that_no_longer_matches_the_evidence_fails_the_run() -> None:
    running = await execution(await create_run())
    await running.constrain_recommendation(
        Recommendation.CLOSE, (CONCERN,), has_profile=True, evidence_gaps=False
    )
    result = await running.constrain_recommendation(
        Recommendation.CLOSE, (), has_profile=False, evidence_gaps=False
    )
    assert result is None
    assert running.run.state == RunState.FAILED
    assert running.run.failure_reason == (
        "Stored recommendation constraint no longer matches the evidence."
    )


def stored_constraint(*, input_blocking: list[str], output_reasons: list[str]) -> TraceEvent:
    return TraceEvent(
        1,
        CONSTRAINT,
        "transition",
        FixedClock().now(),
        input_json=state_json(
            {
                "proposed": "close",
                "blocking_signals": input_blocking,
                "has_profile": True,
                "evidence_gaps": False,
            }
        ),
        output_json=state_json({"recommendation": "escalate", "reasons": output_reasons}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocking", "reasons", "matches"),
    [
        pytest.param(["finding:amount"], ["unresolved_concerns"], True, id="identical"),
        pytest.param(["finding:other"], ["unresolved_concerns"], False, id="other-input-only"),
        pytest.param(["finding:amount"], ["evidence_gaps"], False, id="other-output-only"),
    ],
)
async def test_a_stored_constraint_must_match_both_its_input_and_its_output(
    blocking: list[str], reasons: list[str], matches: bool
) -> None:
    running = await execution(
        await create_run(), stored_constraint(input_blocking=blocking, output_reasons=reasons)
    )
    result = await running.constrain_recommendation(
        Recommendation.CLOSE, (CONCERN,), has_profile=True, evidence_gaps=False
    )
    assert (result == Recommendation.ESCALATE) is matches
    assert (running.run.state == RunState.FAILED) is not matches
