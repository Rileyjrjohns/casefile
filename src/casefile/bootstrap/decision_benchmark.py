"""Compare typed adapters on identical frozen questions, with explicit replay provenance."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from pydantic import JsonValue, TypeAdapter

from casefile.bootstrap.decision_recording import (
    Recording,
    RecordingTransport,
    canonical,
    write_recording,
)
from casefile.bootstrap.decisions import RiskBridge, demo_decisions, require
from casefile.bootstrap.evaluations import (
    CURRENT_PROFILE,
    LEGACY_PROFILE,
    AgentCaseRunner,
    EvaluationProfile,
    ScenarioDocument,
)
from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel
from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.llm import HttpLanguageModel, LlmDecisionModel
from casefile.features.decisioning.adapters.outbound.local import LocalDecisionModel
from casefile.features.decisioning.adapters.outbound.wire import DecisionSettings
from casefile.features.decisioning.application.ports import DecisionModel
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.features.evaluation.adapters.outbound.decision_thresholds import (
    load_decision_thresholds,
)
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.evaluation.application.benchmark import BenchmarkCase
from casefile.features.evaluation.domain.decision_metrics import (
    DecisionMeasurement,
    accuracy_violations,
    summarize,
)
from casefile.features.evaluation.domain.decision_questions import (
    Basis,
    CaseFacts,
    LabelledRequest,
    RecordedRequest,
    label_requests,
    unsupported_claim_control,
)
from casefile.features.evaluation.domain.errors import EvaluationError
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.application.agent_types import CaseContext, EvidenceBatch
from casefile.features.investigation.application.banking_policy import BANKING_KINDS
from casefile.features.investigation.application.decisions import (
    DecisionGateway,
    DecisionRequest,
    DecisionResponse,
    state_json,
)
from casefile.features.investigation.application.guard_policy import guard_state
from casefile.features.investigation.application.tools import ToolName
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.risk_scoring.adapters.outbound.local import LocalRiskModel
from casefile.shared.result import Err, Ok, Result

PROVIDERS = ("local", "jev", "llm")
THRESHOLDS_FILE = "decision-thresholds.toml"


@dataclass(frozen=True, slots=True)
class Probe:
    """A frozen question and its label.

    `origin` separates what the agent really sent from the synthetic negatives that are added
    next to it; `basis` says how independent of the policy under test the label is.
    """

    case_id: str
    suite: str
    question: Question
    expected: str | bool
    basis: Basis
    origin: Literal["recorded", "negative-control"]


@dataclass(frozen=True, slots=True)
class DecisionReport:
    mode: str
    dataset_sha256: str
    current_dataset_sha256: str
    passed: bool
    adapters: dict[str, dict[str, dict[str, str | int | float]]]
    provenance: dict[str, str]
    measured_at: str
    models: dict[str, tuple[str, ...]]
    gate_violations: tuple[str, ...] = ()
    recordings: dict[str, str] = field(default_factory=dict)


class RecordingDecisions:
    """Delegates to the demonstration decisions and keeps every request the agent sends."""

    def __init__(self, inner: DecisionGateway) -> None:
        self.inner = inner
        self.requests: list[DecisionRequest] = []

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        return self.inner.quote(request)

    async def execute(self, request: DecisionRequest) -> DecisionResponse:
        self.requests.append(request)
        return await self.inner.execute(request)


def question(row: LabelledRequest) -> Question:
    return require(Question.create(row.operation, row.purpose, row.state_json, row.options)).value


def probe(row: LabelledRequest, origin: Literal["recorded", "negative-control"]) -> Probe:
    return Probe(row.case_id, row.suite, question(row), row.expected, row.basis, origin)


def guard_negatives(
    facts: CaseFacts, context: CaseContext, batches: tuple[EvidenceBatch, ...]
) -> tuple[LabelledRequest, ...]:
    """Realistic denials, built with the agent's own state builder so formats cannot drift apart.

    The positives are not built here: they are the guard states the agent really sent. The
    negatives are another customer, an unrelated sanctions name, a tool the workflow does not
    allow, and a repeated call. They share the guard facts with the policy, so agreement on them
    is a consistency check (ADR 021).
    """

    def row(tool: ToolName, query: str, seen: tuple[EvidenceBatch, ...]) -> LabelledRequest:
        state = state_json(guard_state(tool, query, context, seen))
        return LabelledRequest(
            facts.case_id, facts.suite, "probability", "guard", state, (), False, "consistency"
        )

    other = (
        "demo-customer-900" if context.subject_id != "demo-customer-900" else "demo-customer-901"
    )
    banking = context.kind in BANKING_KINDS
    first = batches[0]
    return (
        row(ToolName.TRANSACTIONS, other, ()),
        row(
            ToolName.SANCTIONS,
            "Unrelated Counterparty Ltd",
            tuple(b for b in batches if b.tool != ToolName.SANCTIONS),
        ),
        row(ToolName.SANCTIONS if banking else ToolName.DOCUMENTS, context.subject_id, ()),
        row(first.tool, first.query, batches[:1]),
    )


def recorded_request(request: DecisionRequest) -> RecordedRequest:
    return RecordedRequest(
        request.key, request.operation, request.purpose, request.state_json, request.options
    )


async def case_probes(
    profile: EvaluationProfile, case: BenchmarkCase, codec: AgentJsonCodec
) -> tuple[Probe, ...]:
    """Run one case through the agent and label every request it sends."""
    recorder = RecordingDecisions(demo_decisions())
    observation = await AgentCaseRunner(
        decisions=recorder, findings_policy=profile.findings_policy
    ).run(case)
    if observation.error is not None or not recorder.requests:
        raise ValueError(f"Case {case.id} did not complete, so its questions cannot be recorded.")
    scenario = ScenarioDocument.model_validate_json(case.scenario_json)
    context = codec.context(json.dumps(scenario.context))
    batches = tuple(codec.evidence(json.dumps(b)) for b in scenario.evidence)
    facts = CaseFacts(
        case.id,
        profile.name,
        context.kind,
        case.expected.recommendation,
        frozenset(s.key for s in case.expected.signals),
        frozenset(
            (source.tool, source.record_id)
            for s in case.expected.signals
            if s.key.startswith("instruction:")
            for source in s.sources
        ),
        tuple(codec.evidence_json(b) for b in batches),
    )
    labelled = label_requests(facts, tuple(recorded_request(r) for r in recorder.requests))
    return (
        *(probe(row, "recorded") for row in require_evaluation(labelled)),
        *(probe(row, "negative-control") for row in guard_negatives(facts, context, batches)),
        probe(unsupported_claim_control(facts), "negative-control"),
    )


def require_evaluation[T](result: Result[T, EvaluationError]) -> T:
    if isinstance(result, Err):
        raise ValueError(result.error.message)
    return result.value


# Recording runs every case of both suites, so a process records each pair of datasets once.
_RECORDED: dict[tuple[str, str], tuple[Probe, ...]] = {}


async def collect_probes(root: Path) -> tuple[Probe, ...]:
    """Questions of both evaluation suites, recorded from the agent's real runs."""
    suites = [(p, load_suite(root / p.subdirectory)) for p in (LEGACY_PROFILE, CURRENT_PROFILE)]
    key = (suites[0][1].sha256, suites[1][1].sha256)
    if key not in _RECORDED:
        codec = AgentJsonCodec()
        probes: list[Probe] = []
        for profile, suite in suites:
            for case in suite.cases:
                probes.extend(await case_probes(profile, case, codec))
        _RECORDED[key] = tuple(probes)
    return _RECORDED[key]


class ReplayTransport:
    def __init__(self, root: Path, providers: tuple[str, ...] = ("jev", "llm")) -> None:
        self.recordings = {
            name: Recording.model_validate_json((root / f"{name}.json").read_text())
            for name in providers
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        name = "jev" if request.url.path == "/v1/systemone" else "llm"
        key = canonical(json.loads(request.content))
        response = self.recordings[name].responses.get(key)
        if response is None:
            raise ValueError("Decision replay request does not match a frozen recording.")
        return httpx.Response(200, json=response)


def captured(recording: Recording) -> str:
    when = recording.recorded_at
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if when else "at an unknown time"


def provider_transport() -> httpx.AsyncBaseTransport:
    """The network path to a live provider; tests substitute a mock provider here."""
    return httpx.AsyncHTTPTransport(trust_env=False)


def settings(name: str, live: bool, recording: Recording | None = None) -> DecisionSettings:
    """Live settings come from the environment; replay settings come from the recording.

    A replay sends the model name its recording was made with, so the request bodies match the
    recorded keys. Synthetic fixtures name no model and use their own placeholder.
    """
    if not live:
        return DecisionSettings(
            "https://replay.invalid/v1/systemone"
            if name == "jev"
            else "https://replay.invalid/v1/chat/completions",
            (recording.model if recording else None) or f"{name}-synthetic-protocol-v1",
            "",
            Decimal(0),
            Decimal(0),
        )
    prefix = f"CASEFILE_{name.upper()}"
    try:
        if not os.environ[f"{prefix}_API_KEY"].strip():
            raise ValueError(f"{prefix}_API_KEY is required for the selected provider.")
        return DecisionSettings(
            os.environ[f"{prefix}_URL"],
            os.environ[f"{prefix}_MODEL"],
            os.environ[f"{prefix}_API_KEY"],
            Decimal(os.environ[f"{prefix}_INPUT_COST_PER_MILLION"]),
            Decimal(os.environ[f"{prefix}_OUTPUT_COST_PER_MILLION"]),
        )
    except (KeyError, InvalidOperation) as error:
        raise ValueError(
            f"Selected provider {name} requires credentials, model and explicit prices."
        ) from error


async def call(model: DecisionModel, question: Question) -> Answer:
    operation = {"choose": model.choose, "probability": model.probability, "score": model.score}[
        question.operation
    ]
    return await operation(question)


async def write_synthetic_fixtures(root: Path) -> None:
    """Create protocol examples explicitly; never invoked by tests or the benchmark."""
    fake = FakeDecisionModel()
    payloads: dict[str, dict[str, dict[str, JsonValue]]] = {"jev": {}, "llm": {}}
    async with httpx.AsyncClient(trust_env=False) as client:
        jev = JevDecisionModel(client, settings("jev", False))
        llm = HttpLanguageModel(client, settings("llm", False))
        for item in await collect_probes(root):
            q = item.question
            result = await call(fake, q)
            raw: dict[str, JsonValue]
            if q.operation == "probability":
                raw = {"type": "noul", "noul": result.value}
            elif q.operation == "choose":
                raw = {
                    "type": "choice",
                    "choice": result.value,
                    "confidence": result.confidence,
                    "probabilities": dict(zip(q.options, result.probabilities, strict=True)),
                }
            else:
                raw = {
                    "type": "score",
                    "score": result.value,
                    "confidence": result.confidence,
                    "probabilities": {str(i): p for i, p in enumerate(result.probabilities)},
                    "legend": {str(i): option for i, option in enumerate(q.options)},
                }
            payloads["jev"][canonical(jev.body(q))] = {
                "model": "jev-synthetic-protocol-v1",
                "answers": {"decision": raw},
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
            payloads["llm"][canonical(llm.body(q))] = {
                "model": "llm-synthetic-protocol-v1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "value": result.value,
                                    "confidence": result.confidence,
                                    "probabilities": result.probabilities,
                                }
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
    destination = root / "decision-replay"
    destination.mkdir(exist_ok=True)
    for name, responses in payloads.items():
        (destination / f"{name}.json").write_text(
            Recording(
                schema_version=1, provenance="synthetic-protocol-fixture", responses=responses
            ).model_dump_json(indent=2, exclude_none=True)
            + "\n"
        )


def save_recordings(
    destination: Path,
    capture: RecordingTransport,
    recorded_at: datetime,
    configured: Mapping[str, DecisionSettings],
    rows: Mapping[str, list[DecisionMeasurement]],
) -> dict[str, str]:
    """Write one recording per remote provider whose every question completed, and say why not.

    A provider with a failed or budget-stopped question gets no file: its recording would not
    cover what the offline benchmark replays, and the existing file stays as it was.
    """
    status: dict[str, str] = {}
    for name, provider in configured.items():
        failed = [row for row in rows[name] if row.error is not None]
        if failed:
            status[name] = (
                f"not written: {len(failed)} of {len(rows[name])} questions did not complete "
                f"({', '.join(sorted({str(row.error) for row in failed}))}), and a recording "
                "must cover every question"
            )
            continue
        written = write_recording(
            destination / f"{name}.json",
            Recording(
                schema_version=2,
                provenance="provider-recording",
                model=provider.model,
                recorded_at=recorded_at,
                responses=capture.responses[name],
            ),
            (provider.api_key,),
        )
        status[name] = "written" if isinstance(written, Ok) else f"not written: {written.error}"
    return status


async def benchmark(
    root: Path,
    output: Path,
    *,
    live: bool = False,
    providers: tuple[str, ...] = PROVIDERS,
    max_cost: Decimal = Decimal("1"),
    max_tokens: int = 2_500_000,
    thresholds: Path | None = None,
    local: DecisionModel | None = None,
    record_at: datetime | None = None,
) -> DecisionReport:
    """Measure the selected adapters on the recorded questions and apply the accuracy gate.

    `local` replaces the offline adapter, so a test can degrade it; `thresholds` defaults to the
    regression floors stored next to the dataset. A live run given `record_at` also keeps each
    selected remote provider's responses as `decision-replay/<provider>.json`, stamped with that
    instant, but only for a provider whose every question was answered: a recording must cover
    everything the offline benchmark replays.
    """
    if not providers or len(set(providers)) != len(providers) or set(providers) - set(PROVIDERS):
        raise ValueError("Select one or more distinct providers: local, jev, llm.")
    if not max_cost.is_finite() or max_cost <= 0 or max_tokens <= 0:
        raise ValueError("Positive finite comparison budgets are required.")
    if record_at is not None:
        if not live:
            raise ValueError("Recording provider responses requires a live run.")
        if set(providers) == {"local"}:
            raise ValueError("Recording needs a remote provider (jev or llm).")
        if record_at.tzinfo is None:
            raise ValueError("Recording needs a timezone-aware instant.")
    floors = (
        load_decision_thresholds(thresholds or root / THRESHOLDS_FILE)
        if "local" in providers
        else None
    )
    probes = await collect_probes(root)
    remote = tuple(name for name in providers if name != "local")
    replay = ReplayTransport(root / "decision-replay", remote) if not live and remote else None
    adapters: dict[str, dict[str, dict[str, str | int | float]]] = {}
    provenance: dict[str, str] = {}
    observed_models: dict[str, tuple[str, ...]] = {}
    observations: dict[str, list[dict[str, object]]] = {}
    measured_at = datetime.now(UTC).isoformat()
    charged = Usage()
    all_rows: dict[str, list[DecisionMeasurement]] = {}
    configured = {
        name: settings(name, live, replay.recordings[name] if replay else None) for name in remote
    }
    capture = (
        RecordingTransport(provider_transport(), {n: c.url for n, c in configured.items()})
        if record_at
        else None
    )
    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(replay.handle) if replay else capture
    ) as client:
        # Validate every selected provider before making the first billable call.
        models: dict[str, DecisionModel] = {}
        for name in providers:
            if name == "local":
                models[name] = local or LocalDecisionModel(
                    RiskBridge(LocalRiskModel.load_default())
                )
                continue
            if name == "jev":
                models[name] = JevDecisionModel(client, configured[name])
            else:
                models[name] = LlmDecisionModel(HttpLanguageModel(client, configured[name]))
        for name, model in models.items():
            if name == "local":
                provenance[name] = "measured local policy; tabular risk evaluated separately"
            else:
                provenance[name] = (
                    "live provider"
                    if live
                    else replay.recordings[name].provenance
                    if replay
                    else "unknown"
                )
            rows: list[DecisionMeasurement] = []
            answers: list[dict[str, object]] = []
            versions: set[str] = set()
            for item in probes:
                started = perf_counter()
                reserved = model.quote(item.question)
                error = None
                correct, selected = False, 1.0
                usage = Usage()
                result: Answer | None = None
                try:
                    if (
                        charged.cost + reserved.cost > max_cost
                        or charged.tokens + reserved.tokens > max_tokens
                    ):
                        raise ValueError("Comparison budget would be exceeded.")
                    usage = reserved
                    result = await call(model, item.question)
                    versions.add(result.model)
                    usage = result.usage
                    if usage.tokens > reserved.tokens or usage.cost > reserved.cost:
                        raise ValueError("Provider exceeded its reservation.")
                    if item.question.operation == "choose":
                        correct = result.value == item.expected
                        selected = max(result.probabilities)
                    else:
                        p = float(result.value)
                        correct = (p >= 0.5) == item.expected
                        selected = max(p, 1 - p)
                except Exception as failure:
                    error = type(failure).__name__
                charged = Usage(charged.tokens + usage.tokens, charged.cost + usage.cost)
                rows.append(
                    DecisionMeasurement(
                        item.case_id,
                        item.question.purpose,
                        correct,
                        selected,
                        (perf_counter() - started) * 1000,
                        usage.cost,
                        error,
                    )
                )
                answers.append(
                    {
                        **asdict(rows[-1]),
                        "suite": item.suite,
                        "origin": item.origin,
                        "basis": item.basis,
                        "expected": item.expected,
                        "value": result.value if result else None,
                        "model": result.model if result else None,
                    }
                )
            all_rows[name] = rows
            observations[name] = answers
            observed_models[name] = tuple(sorted(versions))
            adapters[name] = {
                purpose: summarize(tuple(r for r in rows if r.purpose == purpose))
                for purpose in sorted({r.purpose for r in rows})
            }
    recordings = (
        save_recordings(root / "decision-replay", capture, record_at, configured, all_rows)
        if capture and record_at
        else {}
    )
    violations = (
        accuracy_violations(
            {purpose: float(metric["accuracy"]) for purpose, metric in adapters["local"].items()},
            floors,
        )
        if floors is not None
        else ()
    )
    report = DecisionReport(
        "live" if live else "offline",
        load_suite(root).sha256,
        load_suite(root / CURRENT_PROFILE.subdirectory).sha256,
        all(r.error is None for rows in all_rows.values() for r in rows)
        and not violations
        and all(status == "written" for status in recordings.values()),
        adapters,
        provenance,
        measured_at,
        observed_models,
        violations,
        recordings,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "decisions.json").write_bytes(TypeAdapter(DecisionReport).dump_json(report, indent=2))
    (output / "decision-observations.json").write_text(
        json.dumps(
            observations,
            default=str,
            indent=2,
        )
        + "\n"
    )
    genuine = {
        name: recording
        for name, recording in (replay.recordings if replay else {}).items()
        if recording.provenance == "provider-recording"
    }
    lines = [
        "# Typed decision comparison",
        "",
        f"Mode: **{report.mode}**. Datasets: `{report.dataset_sha256}` (findings-v1) and "
        f"`{report.current_dataset_sha256}` (current policy).",
        f"Measured at: `{report.measured_at}`.",
        "Observed models: "
        + "; ".join(
            f"{name}: {', '.join(versions) or 'no successful response'}"
            for name, versions in report.models.items()
        )
        + ".",
        "",
        "Synthetic protocol fixtures validate adapters only. Their accuracy, calibration, "
        "latency and zero cost are not measurements of Jev or an LLM. Live rows require "
        "separately configured providers. Local rows measure the explicit policy; tabular "
        "risk has a separate held-out dataset.",
        "",
        *(
            [
                "Provider recordings replay genuine provider answers captured earlier: "
                + "; ".join(
                    f"{name}: requested model `{recording.model}`, recorded {captured(recording)}"
                    for name, recording in genuine.items()
                )
                + ". Their accuracy and calibration describe that provider at that time; their "
                "latency is local replay and their cost is the zero offline rate.",
                "",
            ]
            if genuine
            else []
        ),
        "Identical questions are the decision requests the agent really sent while running "
        "every case of both suites (the 30 findings-v1 regression cases and the current-policy "
        "cases) under the demonstration decisions, tabular risk excluded. Each adapter receives "
        "the same sequence, whatever its own upstream answers would have been. ECE uses ten bins "
        "of selected-outcome probability (not provider certainty). Signal scores are thresholded "
        "at 0.5 and include one unsupported-claim control per case. Guard rows pair the states "
        "the agent sent with synthetic denials (other customer, unrelated sanctions name, "
        "disallowed tool, repeated call). Labels derive from the case expectations where "
        "possible; the basis of each purpose is listed below, and `consistency` rows only check "
        "that an adapter and the label builder agree. Questions come from small, correlated "
        "synthetic cases pending independent human review. A choice with a single option, such as "
        "`model_route` while no fallback narrative model is configured, is resolved locally and is "
        "trivially correct for every adapter.",
        "",
        "| Decision | Questions | Label basis |",
        "| --- | ---: | --- |",
        *(
            f"| {purpose} | {sum(p.question.purpose == purpose for p in probes)} | "
            + ", ".join(sorted({p.basis for p in probes if p.question.purpose == purpose}))
            + " |"
            for purpose in sorted({p.question.purpose for p in probes})
        ),
        "",
        *(
            [
                f"Regression gate for `local` (floors in `{THRESHOLDS_FILE}`, set from a "
                "measurement and not quality targets): "
                + ("**FAIL**." if violations else "**PASS**."),
                *(f"- {violation}" for violation in violations),
                "",
            ]
            if floors is not None
            else []
        ),
        *(
            [
                "Recordings for offline replay: "
                + "; ".join(f"{name}: {status}" for name, status in recordings.items())
                + ".",
                "",
            ]
            if recordings
            else []
        ),
        "| Adapter | Provenance | Decision | N | Accuracy | ECE | p50 / p95 ms | "
        "USD / case | Errors |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, purposes in adapters.items():
        for purpose, metric in purposes.items():
            lines.append(
                f"| {name} | {provenance[name]} | {purpose} | {metric['count']} | "
                f"{float(metric['accuracy']):.3f} | {float(metric['ece']):.3f} | "
                f"{float(metric['latency_p50_ms']):.3f} / {float(metric['latency_p95_ms']):.3f} | "
                f"{metric['cost_per_case']} | {metric['errors']} |"
            )
    (output / "decisions.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare typed decisions on questions recorded from the agent."
    )
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--output", type=Path, default=Path("build/evals"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--record",
        action="store_true",
        help="With --live, keep the successful provider responses as "
        "DATASET/decision-replay/PROVIDER.json so CI can replay them without a key.",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=PROVIDERS,
        default=list(PROVIDERS),
        help="Adapters to measure; only selected remote providers require credentials.",
    )
    parser.add_argument("--max-cost", type=Decimal, default=Decimal("1"))
    parser.add_argument("--max-tokens", type=int, default=2_500_000)
    parser.add_argument(
        "--thresholds",
        type=Path,
        help=f"Accuracy floors for the local adapter (default: DATASET/{THRESHOLDS_FILE}).",
    )
    parser.add_argument(
        "--write-synthetic-fixtures",
        action="store_true",
        help="Regenerate the synthetic protocol fixtures in DATASET/decision-replay and stop.",
    )
    args = parser.parse_args()
    if args.record and not args.live:
        parser.error("--record requires --live.")
    if args.record and not set(args.providers) & {"jev", "llm"}:
        parser.error("--record needs a remote provider (jev or llm) in --providers.")
    if args.write_synthetic_fixtures:
        asyncio.run(write_synthetic_fixtures(args.dataset))
        print(f"Wrote synthetic protocol fixtures to {args.dataset / 'decision-replay'}.")
        raise SystemExit(0)
    try:
        result = asyncio.run(
            benchmark(
                args.dataset,
                args.output,
                live=args.live,
                providers=tuple(args.providers),
                max_cost=args.max_cost,
                max_tokens=args.max_tokens,
                thresholds=args.thresholds,
                record_at=datetime.now(UTC) if args.record else None,
            )
        )
    except (OSError, ValueError) as error:
        parser.exit(2, f"Decision comparison configuration failed ({type(error).__name__}).\n")
    print(f"{'PASS' if result.passed else 'FAIL'}: typed decision comparison ({result.mode}).")
    for violation in result.gate_violations:
        print(f"Accuracy gate: {violation}")
    for name, status in result.recordings.items():
        if status == "written":
            print(f"Recording {name}: written to {args.dataset / 'decision-replay' / name}.json")
        else:
            print(f"Recording {name}: {status}", file=sys.stderr)
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
