"""Connect evaluation ports to isolated investigation runs and exact replay adapters."""

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from casefile.bootstrap.agent import StoredInvestigationContext
from casefile.bootstrap.decisions import demo_decisions
from casefile.bootstrap.demo_agent import SystemClock
from casefile.bootstrap.memory import build_memory_application
from casefile.features.case_intake.application.open_case import OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.evaluation.adapters.outbound.golden import load_suite, load_thresholds
from casefile.features.evaluation.adapters.outbound.reports import write_report
from casefile.features.evaluation.application.benchmark import (
    BenchmarkCase,
    EvaluateSuite,
    SuiteReport,
)
from casefile.features.evaluation.domain.benchmark import Citation, Observation, ObservedSignal
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.http_model import (
    HttpLanguageModel,
    ModelSettings,
)
from casefile.features.investigation.adapters.outbound.agent.models import ReplayLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import MemoryTelemetry
from casefile.features.investigation.adapters.outbound.agent.tools import ReplayEvidenceGateway
from casefile.features.investigation.adapters.outbound.documents import BudgetData
from casefile.features.investigation.application.agent import InvestigationAgent, checked
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    EvidenceBatch,
    LanguageModel,
    TraceEvent,
    tool_input,
)
from casefile.features.investigation.application.banking_policy import BANKING_KINDS
from casefile.features.investigation.application.decisions import DecisionGateway
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    FINDINGS_VERSIONS,
    LEGACY_FINDINGS,
)
from casefile.features.investigation.application.start_run import StartRunCommand
from casefile.features.investigation.application.worker import RunWorker
from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.shared.ids import CaseId, RunId


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioDocument(Document):
    context: dict[str, JsonValue]
    evidence: tuple[dict[str, JsonValue], ...]


class ReplayDocument(Document):
    schema_version: Literal[1]
    provenance: Literal["synthetic-fixture"]
    request: dict[str, JsonValue]
    response: dict[str, JsonValue]
    reservation: BudgetData


RECORDED_AT = datetime(2026, 10, 24, 9, tzinfo=UTC)


def call_events(
    events: list[TraceEvent], operation: str, input_json: str, output_json: str, usage: BudgetUsage
) -> None:
    """Append the started and succeeded events of one recorded call."""
    events.append(
        TraceEvent(
            len(events) + 1,
            operation,
            "started",
            RECORDED_AT,
            input_json=input_json,
            reserved=usage,
        )
    )
    events.append(
        TraceEvent(
            len(events) + 1,
            operation,
            "succeeded",
            RECORDED_AT,
            input_json=input_json,
            output_json=output_json,
            reserved=usage,
        )
    )


def tool_recordings(
    scenario: ScenarioDocument, codec: AgentJsonCodec
) -> tuple[CaseContext, tuple[EvidenceBatch, ...], list[TraceEvent]]:
    """The case context, its evidence snapshots and one recorded call per evidence tool."""
    context = codec.context(json.dumps(scenario.context))
    batches = tuple(codec.evidence(json.dumps(batch)) for batch in scenario.evidence)
    if len({batch.tool for batch in batches}) != len(batches):
        raise ValueError("Each evidence tool must have one recorded response.")
    events: list[TraceEvent] = []
    for batch in batches:
        call_events(
            events,
            f"tool.{batch.tool}",
            tool_input(batch.tool, batch.query),
            codec.evidence_json(batch),
            checked(BudgetUsage.create(1, 0, Decimal(0))),
        )
    return context, batches, events


def recordings(
    case: BenchmarkCase, codec: AgentJsonCodec
) -> tuple[CaseContext, tuple[TraceEvent, ...]]:
    scenario = ScenarioDocument.model_validate_json(case.scenario_json)
    replay = ReplayDocument.model_validate_json(case.replay_json)
    context, batches, events = tool_recordings(scenario, codec)
    request = codec.request(json.dumps(replay.request))
    response = codec.response(json.dumps(replay.response))
    if request.context != context or request.evidence != batches:
        raise ValueError("Replay inputs differ from the golden scenario.")
    observed = {
        Citation(batch.tool.value, record.record_id)
        for batch in batches
        for record in batch.records
    }
    if any(source not in observed for signal in case.expected.signals for source in signal.sources):
        raise ValueError("Reference analysis cites a record absent from its tool snapshot.")
    reservation = checked(
        BudgetUsage.create(
            replay.reservation.tool_calls,
            replay.reservation.tokens,
            replay.reservation.estimated_cost,
        )
    )
    call_events(
        events,
        "model.generate",
        codec.request_json(request),
        codec.response_json(response),
        reservation,
    )
    return context, tuple(events)


@dataclass(frozen=True, slots=True)
class EvaluationProfile:
    """A reference suite and the findings policy it is evaluated under."""

    name: str
    label: str
    subdirectory: str
    findings_policy: str


# The frozen 30 cases are specified against the historical rules and stay comparable over time.
LEGACY_PROFILE = EvaluationProfile("legacy", "findings-v1 regression profile", "", LEGACY_FINDINGS)
# The current suite runs the policy that new investigations use, including banking workflows.
CURRENT_PROFILE = EvaluationProfile(
    "current", "current-policy profile (findings-v2)", "current", CURRENT_FINDINGS
)
PROFILES = {profile.name: profile for profile in (LEGACY_PROFILE, CURRENT_PROFILE)}
# Document dossiers read six evidence tools; the historical alerts read five.
BANKING_TOOL_CALLS = 6
CORE_TOOL_CALLS = 5


@dataclass(frozen=True, slots=True)
class AgentCaseRunner:
    model: LanguageModel | None = None
    decisions: DecisionGateway | None = None
    findings_policy: str = CURRENT_FINDINGS

    def __post_init__(self) -> None:
        if self.findings_policy not in FINDINGS_VERSIONS:
            raise ValueError("Unknown findings policy version.")

    async def run(self, case: BenchmarkCase) -> Observation:
        return await self.observe(case.id, lambda codec: recordings(case, codec))

    async def observe(
        self,
        key: str,
        load: Callable[[AgentJsonCodec], tuple[CaseContext, tuple[TraceEvent, ...]]],
    ) -> Observation:
        """Run one isolated investigation over recorded tool calls and observe its outcome."""
        started = perf_counter()
        clock, codec = SystemClock(), AgentJsonCodec()
        app = build_memory_application(clock)
        case_id = CaseId(uuid5(NAMESPACE_URL, f"casefile:eval:{key}"))
        run_id = RunId(uuid5(case_id, "run"))
        error: str | None = None
        try:
            context, events = load(codec)
            checked(
                await app.open_case.execute(
                    OpenCaseCommand(
                        case_id,
                        context.alert_id,
                        context.subject_id,
                        AlertKind(context.kind),
                        context.description,
                    )
                )
            )
            checked(
                await app.start_run.execute(
                    StartRunCommand(
                        run_id,
                        case_id,
                        checked(
                            BudgetLimits.create(
                                BANKING_TOOL_CALLS
                                if context.kind in BANKING_KINDS
                                else CORE_TOOL_CALLS,
                                100_000,
                                Decimal("1"),
                            )
                        ),
                    )
                )
            )
            agent = InvestigationAgent(
                app.journal,
                StoredInvestigationContext(app.cases),
                ReplayEvidenceGateway(events, codec),
                self.model or ReplayLanguageModel(events, codec),
                codec,
                clock,
                MemoryTelemetry(),
                self.decisions or demo_decisions(),
                findings_policy=self.findings_policy,
            )
            await RunWorker(app.queue, agent).run_once()
        except Exception as failure:
            # Provider messages can contain credentials; keep only their technical category.
            error = type(failure).__name__
        elapsed = (perf_counter() - started) * 1000
        snapshot = await app.journal.load(run_id)
        if snapshot is None:
            return Observation("ERROR", (), None, (), Decimal(0), 0, elapsed, error)
        run = snapshot.snapshot.run
        keys = {
            uuid5(run_id, signal.key): signal.key
            for event in snapshot.events
            if event.operation == "model.generate" and event.phase == "started"
            for signal in codec.request(event.input_json).signals
        }
        analysis = run.analysis
        model = next(
            (
                codec.response(event.output_json).model
                for event in reversed(snapshot.events)
                if event.operation == "model.generate"
                and event.phase == "succeeded"
                and event.output_json is not None
            ),
            None,
        )
        signals = (
            tuple(
                ObservedSignal(
                    keys.get(conclusion.id, f"unknown:{conclusion.id}"),
                    tuple(Citation(source.tool, source.record_id) for source in conclusion.sources),
                    conclusion.statement,
                    conclusion.confidence.value,
                )
                for conclusion in analysis.conclusions
            )
            if analysis
            else ()
        )
        return Observation(
            run.state.value,
            signals,
            analysis.recommendation.value if analysis else None,
            tuple(Citation(source.tool, source.record_id) for source in run.sources),
            run.usage.estimated_cost,
            run.usage.tokens,
            elapsed,
            error or ("RunFailed" if run.failure_reason else None),
            model,
            analysis.summary if analysis else None,
        )


async def run_evaluations(
    root: Path,
    output: Path,
    *,
    profile: EvaluationProfile,
    model: LanguageModel | None = None,
) -> SuiteReport:
    suite = load_suite(root)
    report = await EvaluateSuite(
        AgentCaseRunner(model, findings_policy=profile.findings_policy)
    ).execute(
        suite.cases,
        load_thresholds(root / "thresholds.toml"),
        mode="http" if model is not None else "replay",
        dataset_sha256=suite.sha256,
        profile=profile.label,
        findings_policy=profile.findings_policy,
    )
    write_report(report, output)
    return report


def selected_profiles(name: str) -> tuple[EvaluationProfile, ...]:
    return tuple(PROFILES.values()) if name == "all" else (PROFILES[name],)


async def execute_cli(
    dataset: Path, output: Path, mode: str, profiles: tuple[EvaluationProfile, ...]
) -> tuple[SuiteReport, ...]:
    """Run each profile from `dataset/<subdirectory>` and report to `output/<subdirectory>`."""
    reports: list[SuiteReport] = []
    if mode == "replay":
        for profile in profiles:
            reports.append(
                await run_evaluations(
                    dataset / profile.subdirectory, output / profile.subdirectory, profile=profile
                )
            )
        return tuple(reports)
    settings = ModelSettings.from_environment(os.environ)
    async with httpx.AsyncClient(trust_env=False) as client:
        model = HttpLanguageModel(client, settings, AgentJsonCodec())
        for profile in profiles:
            reports.append(
                await run_evaluations(
                    dataset / profile.subdirectory,
                    output / profile.subdirectory,
                    profile=profile,
                    model=model,
                )
            )
    return tuple(reports)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fictional cases and enforce thresholds.")
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--output", type=Path, default=Path("build/evals"))
    parser.add_argument("--mode", choices=("replay", "http"), default="replay")
    parser.add_argument(
        "--profile",
        choices=("all", *PROFILES),
        default="all",
        help="legacy: frozen findings-v1 cases; current: the policy new investigations use.",
    )
    args = parser.parse_args()
    profiles = selected_profiles(args.profile)
    try:
        reports = asyncio.run(execute_cli(args.dataset, args.output, args.mode, profiles))
    except (OSError, ValueError) as error:
        # Configuration errors must fail, without printing environment credentials.
        parser.exit(2, f"Evaluation configuration failed ({type(error).__name__}).\n")
    for profile, report in zip(profiles, reports, strict=True):
        where = args.output / profile.subdirectory
        print(
            f"{'PASS' if report.passed else 'FAIL'}: {profile.label}, "
            f"{report.metrics.cases} cases ({report.mode})."
        )
        print(f"Reports: {where / 'report.md'} and {where / 'report.json'}")
    raise SystemExit(0 if all(report.passed for report in reports) else 1)


if __name__ == "__main__":
    main()
