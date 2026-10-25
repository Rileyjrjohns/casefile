"""Current findings handle monthly totals and unresolved screening alerts."""

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from casefile.bootstrap.agent import StoredInvestigationContext
from casefile.bootstrap.decisions import demo_decisions
from casefile.bootstrap.evaluations import recordings
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.decisions import ReplayDecisionGateway
from casefile.features.investigation.adapters.outbound.agent.models import (
    FakeLanguageModel,
    ReplayLanguageModel,
)
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import ReplayEvidenceGateway
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.agent_policy import candidates
from casefile.features.investigation.application.agent_types import EvidenceBatch, TraceEvent
from casefile.features.investigation.application.tools import AlertRecord, ToolName, Transaction
from casefile.features.investigation.application.worker import RunWorker
from tests.contracts.support import FixedClock, value
from tests.test_agent_workflow import RUN, create_run


def evidence(number: int) -> tuple[EvidenceBatch, ...]:
    case = next(c for c in load_suite(Path("evals")).cases if c.id == f"case-{number:03}")
    _, tape = recordings(case, AgentJsonCodec())
    return tuple(
        AgentJsonCodec().evidence(e.output_json)
        for e in tape
        if e.operation.startswith("tool.") and e.phase == "succeeded" and e.output_json
    )


def mutate_transactions(
    batches: tuple[EvidenceBatch, ...], transform: Callable[[Transaction], Transaction]
) -> tuple[EvidenceBatch, ...]:
    return tuple(
        replace(
            b,
            records=tuple(
                transform(r) if isinstance(r, Transaction) and r.record_id == "txn-002" else r
                for r in b.records
            ),
        )
        for b in batches
    )


def test_cumulative_monthly_volume_is_sourced_to_all_matching_transfers() -> None:
    signals = candidates(evidence(9), frozenset(), policy_version="findings-v2")
    monthly = next(s for s in signals if s.key.startswith("monthly_volume:"))
    assert "6000.00" in monthly.statement and "5000.00" in monthly.statement
    assert {s.record_id for s in monthly.sources} == {"kyc-001", "txn-001", "txn-002"}
    assert not candidates(evidence(9), frozenset(), policy_version="findings-v1")


def test_months_and_currencies_are_not_combined() -> None:
    batches = evidence(9)
    transaction = next(r for b in batches for r in b.records if isinstance(r, Transaction))
    later = mutate_transactions(
        batches, lambda r: replace(r, booked_at=transaction.booked_at + timedelta(days=40))
    )
    assert not any(s.key.startswith("monthly_volume:") for s in candidates(later, frozenset()))
    foreign = mutate_transactions(batches, lambda r: replace(r, currency="USD"))
    signals = candidates(foreign, frozenset())
    assert not any(s.key.startswith("monthly_volume:") for s in signals)
    assert any(s.key.startswith("gap:currency:") for s in signals)
    boundary = mutate_transactions(batches, lambda r: replace(r, amount=Decimal("2000.00")))
    assert not any(s.key.startswith("monthly_volume:") for s in candidates(boundary, frozenset()))


def test_repeated_evidence_does_not_double_count_transaction_ids() -> None:
    batches = evidence(9)
    transaction_batch = next(b for b in batches if b.tool == ToolName.TRANSACTIONS)
    signals = candidates((*batches, transaction_batch), frozenset())
    monthly = [s for s in signals if s.key.startswith("monthly_volume:")]
    assert len(monthly) == 1 and "6000.00" in monthly[0].statement


def test_unmatched_open_sanctions_alert_requires_information_not_a_name_match_claim() -> None:
    signals = candidates(evidence(15), frozenset())
    assert len(signals) == 1 and signals[0].key == "gap:unresolved_sanctions:alert-001"
    assert signals[0].sources[0].record_id == "alert-001"
    assert "does not resolve" in signals[0].statement
    assert not candidates(evidence(15), frozenset(), policy_version="findings-v1")


def test_resolved_screening_and_routine_activity_do_not_create_new_concerns() -> None:
    resolved = tuple(
        replace(
            b,
            records=tuple(
                replace(r, status="closed") if isinstance(r, AlertRecord) else r for r in b.records
            ),
        )
        for b in evidence(15)
    )
    assert not candidates(resolved, frozenset())
    assert not candidates(evidence(1), frozenset())
    assert not any(
        s.key.startswith("gap:unresolved_sanctions:") for s in candidates(evidence(13), frozenset())
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_version", [False, True])
async def test_started_legacy_run_keeps_its_policy_after_an_upgrade(with_version: bool) -> None:
    case = next(c for c in load_suite(Path("evals")).cases if c.id == "case-009")
    codec = AgentJsonCodec()
    context, tape = recordings(case, codec)
    app = await create_run(subject=context.subject_id, kind=AlertKind(context.kind))
    lease = await app.queue.claim(lease_for=timedelta(seconds=60))
    assert lease is not None
    current = await app.journal.load(RUN)
    assert current is not None
    if with_version:
        current = value(
            await app.journal.checkpoint(
                lease,
                current.snapshot.run,
                TraceEvent(
                    1,
                    "policy.findings",
                    "transition",
                    FixedClock().now(),
                    output_json='{"version":"findings-v1"}',
                ),
                expected_revision=current.snapshot.revision,
            )
        )
    current = value(
        await app.journal.checkpoint(
            lease,
            value(current.snapshot.run.start_gathering()),
            TraceEvent(
                len(current.events) + 1,
                "case_context",
                "transition",
                FixedClock().now(),
                output_json=codec.context_json(context),
            ),
            expected_revision=current.snapshot.revision,
        )
    )
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        ReplayEvidenceGateway(tape, codec),
        FakeLanguageModel(),
        codec,
        FixedClock(),
        MemoryTelemetry(),
        demo_decisions(),
    )
    await agent.execute(lease)
    completed = await app.journal.load(RUN)
    assert completed is not None and completed.snapshot.run.analysis is not None
    assert completed.snapshot.run.analysis.recommendation.value == "close"
    assert not completed.snapshot.run.analysis.conclusions


@pytest.mark.asyncio
@pytest.mark.parametrize("number,expected", [(9, "escalate"), (15, "request_information")])
async def test_current_workflow_records_policy_and_replays_new_findings(
    number: int, expected: str
) -> None:
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
        demo_decisions(),
    )
    await RunWorker(app.queue, agent).run_once()
    recorded = await app.journal.load(RUN)
    assert recorded is not None and recorded.snapshot.run.analysis is not None
    assert recorded.snapshot.run.analysis.recommendation.value == expected
    assert any(
        e.operation == "policy.findings" and "findings-v2" in (e.output_json or "")
        for e in recorded.events
    )
    fresh = await create_run(subject=context.subject_id, kind=AlertKind(context.kind))
    replay = replace(
        agent,
        journal=fresh.journal,
        contexts=StoredInvestigationContext(fresh.cases),
        tools=ReplayEvidenceGateway(recorded.events, codec),
        model=ReplayLanguageModel(recorded.events, codec),
        decisions=ReplayDecisionGateway(recorded.events),
    )
    await RunWorker(fresh.queue, replay).run_once()
    assert await fresh.journal.load(RUN) == recorded
