"""A provider cannot recommend closure while established concerns remain."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from casefile.bootstrap.agent import StoredInvestigationContext, build_agent_worker
from casefile.bootstrap.api import create_app
from casefile.bootstrap.decisions import DecisionBridge
from casefile.bootstrap.evaluations import recordings
from casefile.bootstrap.memory import MemoryApplication
from casefile.bootstrap.runtime import Runtime
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel, answer
from casefile.features.decisioning.domain.decisions import Answer, Question
from casefile.features.evaluation.adapters.outbound.golden import load_suite
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
from casefile.features.investigation.application.agent_types import Signal, SourceLink
from casefile.features.investigation.application.recommendation_policy import constrain_closure
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.run import RunState
from tests.contracts.support import FixedClock
from tests.test_agent_workflow import RUN, create_run


class ClosingDecision(FakeDecisionModel):
    async def choose(self, question: Question) -> Answer:
        if question.purpose == "recommendation":
            return answer(question, "close")
        return await super().choose(question)

    async def score(self, question: Question) -> Answer:
        # A second model cannot erase a detected instruction by lowering its support score.
        return answer(question, 0.0)


def signal(key: str, confidence: float = 0.0) -> Signal:
    return Signal(key, "Observed fictional fact.", confidence, (SourceLink("test", "record"),))


@pytest.mark.parametrize("key", ["instruction:note", "kyc:alert", "finding:income", "volume:txn"])
def test_observed_concerns_block_closure_despite_a_low_model_score(key: str) -> None:
    result = constrain_closure(Recommendation.CLOSE, (signal(key),), has_profile=True)
    assert result.recommendation == Recommendation.ESCALATE
    assert result.blocking_signals == (key,)
    assert result.reasons == ("unresolved_concerns",)


@pytest.mark.parametrize("probability,expected", [(0.499, "close"), (0.5, "escalate")])
def test_risk_threshold_distinguishes_informational_and_concerning_predictions(
    probability: float, expected: str
) -> None:
    result = constrain_closure(
        Recommendation.CLOSE, (signal("risk:txn", probability),), has_profile=True
    )
    assert result.recommendation.value == expected


@pytest.mark.parametrize("has_profile,gaps", [(False, False), (True, True)])
def test_missing_information_blocks_closure_without_inventing_a_fraud_signal(
    has_profile: bool, gaps: bool
) -> None:
    result = constrain_closure(
        Recommendation.CLOSE, (), has_profile=has_profile, evidence_gaps=gaps
    )
    assert result.recommendation == Recommendation.REQUEST_INFORMATION
    assert not result.blocking_signals
    assert result.reasons == (("missing_kyc",) if not has_profile else ("evidence_gaps",))


def test_gap_signal_blocks_closure_and_mitigating_context_does_not() -> None:
    context = (signal("context:distinct_identity"),)
    assert (
        constrain_closure(Recommendation.CLOSE, context, has_profile=True).recommendation
        == Recommendation.CLOSE
    )
    result = constrain_closure(
        Recommendation.CLOSE, (*context, signal("gap:identity")), has_profile=True
    )
    assert result.recommendation == Recommendation.REQUEST_INFORMATION
    assert result.reasons == ("evidence_gaps",)


@pytest.mark.parametrize(
    "recommendation", [Recommendation.ESCALATE, Recommendation.REQUEST_INFORMATION]
)
def test_nonclosing_advice_remains_a_model_choice(recommendation: Recommendation) -> None:
    result = constrain_closure(recommendation, (signal("instruction:note"),), has_profile=False)
    assert result.recommendation == recommendation
    assert not result.reasons


async def injection_run(number: int) -> tuple[MemoryApplication, InvestigationAgent]:
    case = next(c for c in load_suite(Path("evals")).cases if c.id == f"case-{number:03}")
    codec = AgentJsonCodec()
    context, tape = recordings(case, codec)
    app = await create_run(subject=context.subject_id, kind=AlertKind(context.kind))
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        ReplayEvidenceGateway(tape, codec),
        FakeLanguageModel(),
        codec,
        FixedClock(),
        MemoryTelemetry(),
        DecisionBridge(ClosingDecision()),
    )
    return app, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [21, 22, 28])
async def test_injected_case_rejects_model_closure_and_exposes_auditable_adjustment(
    number: int,
) -> None:
    app, agent = await injection_run(number)
    runtime = Runtime(app, RunWorker(app.queue, agent))
    assert await runtime.worker.run_once()
    api = create_app(runtime, background_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        detail = (await client.get(f"/runs/{RUN}")).json()
        run = detail["run"]
        assert run["state"] == "AWAITING_REVIEW"
        assert run["human_decision"] is None
        assert run["analysis"]["recommendation"] == "escalate"
        raw = next(
            e
            for e in detail["events"]
            if e["operation"] == "decision.recommendation.final" and e["phase"] == "succeeded"
        )
        assert json.loads(raw["output_json"])["value"] == "close"
        adjustments = [e for e in detail["events"] if e["operation"] == "policy.recommendation.v1"]
        assert len(adjustments) == 1
        assert adjustments[0]["reserved"] is adjustments[0]["actual"] is None
        assert json.loads(adjustments[0]["output_json"])["recommendation"] == "escalate"
        assert (
            "instruction:note-001" in json.loads(adjustments[0]["input_json"])["blocking_signals"]
        )
        for conclusion in run["analysis"]["conclusions"]:
            for source in conclusion["sources"]:
                assert (
                    await client.get(f"/runs/{RUN}/sources/{source['tool']}/{source['record_id']}")
                ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "number,kind,expected",
    [
        (101, AlertKind.PAYMENT_FRAUD, Recommendation.ESCALATE),
        (102, AlertKind.CREDIT_DOCUMENT_FRAUD, Recommendation.ESCALATE),
        (103, AlertKind.SANCTIONS_IDENTITY_REVIEW, Recommendation.CLOSE),
    ],
)
async def test_banking_discrepancies_block_closure_but_a_resolved_homonym_can_close(
    number: int, kind: AlertKind, expected: Recommendation
) -> None:
    app = await create_run(tools=6, subject=f"demo-customer-{number}", kind=kind)
    await build_agent_worker(
        app,
        MemoryInvestigationTools(load_seed(banking=True)),
        FakeLanguageModel(),
        FixedClock(),
        MemoryTelemetry(),
        decisions=DecisionBridge(ClosingDecision()),
    ).run_once()
    current = await app.journal.load(RUN)
    assert current is not None and current.snapshot.run.analysis is not None
    assert current.snapshot.run.analysis.recommendation == expected
    assert current.snapshot.run.human_decision is None


@pytest.mark.asyncio
async def test_adjusted_recommendation_trace_replays_exactly() -> None:
    original, agent = await injection_run(21)
    await RunWorker(original.queue, agent).run_once()
    recorded = await original.journal.load(RUN)
    assert recorded is not None
    fresh, replay = await injection_run(21)
    codec = AgentJsonCodec()
    replay = replace(
        replay,
        tools=ReplayEvidenceGateway(recorded.events, codec),
        model=ReplayLanguageModel(recorded.events, codec),
        decisions=ReplayDecisionGateway(recorded.events),
    )
    await RunWorker(fresh.queue, replay).run_once()
    assert await fresh.journal.load(RUN) == recorded


@pytest.mark.asyncio
async def test_retry_after_adjustment_does_not_duplicate_policy_or_provider_calls() -> None:
    failures: list[str] = []

    class RetryRoute(ClosingDecision):
        async def choose(self, question: Question) -> Answer:
            if question.purpose == "model_route" and not failures:
                failures.append(question.purpose)
                raise ValueError("Transient narrative routing failure")
            return await super().choose(question)

    app, agent = await injection_run(21)
    agent = replace(agent, decisions=DecisionBridge(RetryRoute()))
    worker = RunWorker(app.queue, agent, retry_delay=timedelta(0))
    with pytest.raises(ValueError):
        await worker.run_once()
    assert await worker.run_once()
    snapshot = await app.journal.load(RUN)
    assert snapshot is not None and snapshot.snapshot.run.state == RunState.AWAITING_REVIEW
    assert sum(e.operation == "policy.recommendation.v1" for e in snapshot.events) == 1
    assert (
        sum(
            e.operation == "decision.recommendation.final" and e.phase == "started"
            for e in snapshot.events
        )
        == 1
    )
