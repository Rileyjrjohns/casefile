"""Compare typed adapters on identical frozen questions, with explicit replay provenance."""

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from casefile.bootstrap.decisions import RiskBridge, require
from casefile.bootstrap.evaluations import ScenarioDocument
from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel
from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.llm import HttpLanguageModel, LlmDecisionModel
from casefile.features.decisioning.adapters.outbound.local import LocalDecisionModel
from casefile.features.decisioning.adapters.outbound.wire import DecisionSettings
from casefile.features.decisioning.application.ports import DecisionModel
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.features.decisioning.domain.injection import looks_like_instruction
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.evaluation.domain.decision_metrics import DecisionMeasurement, summarize
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.application.agent_policy import candidates
from casefile.features.investigation.application.agent_types import CaseContext, EvidenceBatch
from casefile.features.investigation.application.banking_policy import BANKING_KINDS
from casefile.features.investigation.application.decisions import state_json
from casefile.features.investigation.application.guard_policy import guard_state
from casefile.features.investigation.application.tools import (
    InvestigatorNote,
    KycProfile,
    ToolName,
)
from casefile.features.risk_scoring.adapters.outbound.local import LocalRiskModel

PROVIDERS = ("local", "jev", "llm")


@dataclass(frozen=True, slots=True)
class Probe:
    case_id: str
    question: Question
    expected: str | bool


@dataclass(frozen=True, slots=True)
class DecisionReport:
    mode: str
    dataset_sha256: str
    passed: bool
    adapters: dict[str, dict[str, dict[str, str | int | float]]]
    provenance: dict[str, str]
    measured_at: str
    models: dict[str, tuple[str, ...]]


def guard_probes(
    case_id: str, context: CaseContext, batches: tuple[EvidenceBatch, ...]
) -> tuple[Probe, ...]:
    """Guard questions use the agent's own state builder, so formats cannot drift apart.

    Positives are the states the agent sends for this case's recorded tool calls. Negatives are
    realistic denials: another customer, an unrelated sanctions name, a tool the workflow does
    not allow, and a repeated call.
    """

    def probe(tool: ToolName, query: str, seen: tuple[EvidenceBatch, ...], allowed: bool) -> Probe:
        state = state_json(guard_state(tool, query, context, seen))
        return Probe(
            case_id, require(Question.create("probability", "guard", state)).value, allowed
        )

    other = (
        "demo-customer-900" if context.subject_id != "demo-customer-900" else "demo-customer-901"
    )
    banking = context.kind in BANKING_KINDS
    first = batches[0]
    return (
        *(probe(b.tool, b.query, batches[:i], True) for i, b in enumerate(batches)),
        probe(ToolName.TRANSACTIONS, other, (), False),
        probe(
            ToolName.SANCTIONS,
            "Unrelated Counterparty Ltd",
            tuple(b for b in batches if b.tool != ToolName.SANCTIONS),
            False,
        ),
        probe(ToolName.SANCTIONS if banking else ToolName.DOCUMENTS, context.subject_id, (), False),
        probe(first.tool, first.query, batches[:1], False),
    )


def probes(root: Path) -> tuple[Probe, ...]:
    result: list[Probe] = []
    codec = AgentJsonCodec()
    for case in load_suite(root).cases:
        scenario = ScenarioDocument.model_validate_json(case.scenario_json)
        context = codec.context(json.dumps(scenario.context))
        batches = tuple(codec.evidence(json.dumps(b)) for b in scenario.evidence)
        records = tuple(r for b in batches for r in b.records)
        injections = frozenset(
            r.record_id
            for r in records
            if isinstance(r, InvestigatorNote) and looks_like_instruction(r.text)
        )
        facts = candidates(batches, injections, policy_version="findings-v1")
        base: dict[str, object] = {
            "case_id": case.id,
            "kind": context.kind,
            "description": context.description,
            "evidence": scenario.evidence,
        }

        def add(
            operation: str,
            purpose: str,
            state: dict[str, object],
            expected: str | bool,
            options: tuple[str, ...] = (),
            *,
            base: dict[str, object] = base,
            case_id: str = case.id,
        ) -> None:
            q = require(
                Question.create(
                    operation, purpose, json.dumps({**base, **state}, sort_keys=True), options
                )
            ).value
            result.append(Probe(case_id, q, expected))

        add(
            "choose",
            "priority",
            {},
            "high" if context.kind == "sanctions_match" else "normal",
            ("normal", "high"),
        )
        add(
            "choose",
            "alert_type",
            {},
            context.kind,
            ("unusual_transaction", "sanctions_match", "kyc_inconsistency"),
        )
        add(
            "choose",
            "tool_route",
            {"available": ["get_kyc_profile", "list_transactions"]},
            "get_kyc_profile",
            ("get_kyc_profile", "list_transactions"),
        )
        add(
            "choose",
            "model_route",
            {"signal_count": len(facts)},
            "primary",
            ("primary", "conservative"),
        )
        result.extend(guard_probes(case.id, context, batches))
        add(
            "probability",
            "injection",
            {"text": json.dumps(scenario.evidence)},
            any(s.key.startswith("instruction:") for s in case.expected.signals),
        )
        expected_keys = {s.key for s in case.expected.signals}
        for fact in facts:
            add(
                "score",
                "signal",
                {"signal_kind": fact.key.split(":")[0], "statement": fact.statement},
                fact.key in expected_keys,
                ("unsupported", "fully supported"),
            )
        # Negative controls exist independently of whether a case already has positive signals.
        add(
            "score",
            "signal",
            {
                "signal_kind": "unsupported",
                "statement": "This case has a verified criminal conviction; "
                "no such record is supplied.",
            },
            False,
            ("unsupported", "fully supported"),
        )
        add(
            "choose",
            "recommendation",
            {
                "signal_count": len(facts),
                "has_profile": any(isinstance(r, KycProfile) for r in records),
            },
            case.expected.recommendation,
            ("close", "escalate", "request_information"),
        )
    return tuple(result)


def canonical(body: object) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    provenance: Literal["synthetic-protocol-fixture", "provider-recording"]
    responses: dict[str, dict[str, JsonValue]]


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


def settings(name: str, live: bool) -> DecisionSettings:
    if not live:
        return DecisionSettings(
            "https://replay.invalid/v1/systemone"
            if name == "jev"
            else "https://replay.invalid/v1/chat/completions",
            f"{name}-synthetic-protocol-v1",
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
        for probe in probes(root):
            q = probe.question
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
            ).model_dump_json(indent=2)
            + "\n"
        )


async def benchmark(
    root: Path,
    output: Path,
    *,
    live: bool = False,
    providers: tuple[str, ...] = PROVIDERS,
    max_cost: Decimal = Decimal("1"),
    max_tokens: int = 2_500_000,
) -> DecisionReport:
    if not providers or len(set(providers)) != len(providers) or set(providers) - set(PROVIDERS):
        raise ValueError("Select one or more distinct providers: local, jev, llm.")
    if not max_cost.is_finite() or max_cost <= 0 or max_tokens <= 0:
        raise ValueError("Positive finite comparison budgets are required.")
    remote = tuple(name for name in providers if name != "local")
    replay = ReplayTransport(root / "decision-replay", remote) if not live and remote else None
    adapters: dict[str, dict[str, dict[str, str | int | float]]] = {}
    provenance: dict[str, str] = {}
    observed_models: dict[str, tuple[str, ...]] = {}
    observations: dict[str, list[dict[str, object]]] = {}
    measured_at = datetime.now(UTC).isoformat()
    charged = Usage()
    all_rows: dict[str, list[DecisionMeasurement]] = {}
    async with httpx.AsyncClient(
        trust_env=False, transport=httpx.MockTransport(replay.handle) if replay else None
    ) as client:
        # Validate every selected provider before making the first billable call.
        models: dict[str, DecisionModel] = {}
        for name in providers:
            if name == "local":
                models[name] = LocalDecisionModel(RiskBridge(LocalRiskModel.load_default()))
            elif name == "jev":
                models[name] = JevDecisionModel(client, settings(name, live))
            else:
                models[name] = LlmDecisionModel(HttpLanguageModel(client, settings(name, live)))
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
            for probe in probes(root):
                started = perf_counter()
                reserved = model.quote(probe.question)
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
                    result = await call(model, probe.question)
                    versions.add(result.model)
                    usage = result.usage
                    if usage.tokens > reserved.tokens or usage.cost > reserved.cost:
                        raise ValueError("Provider exceeded its reservation.")
                    if probe.question.operation == "choose":
                        correct = result.value == probe.expected
                        selected = max(result.probabilities)
                    else:
                        p = float(result.value)
                        correct = (p >= 0.5) == probe.expected
                        selected = max(p, 1 - p)
                except Exception as failure:
                    error = type(failure).__name__
                charged = Usage(charged.tokens + usage.tokens, charged.cost + usage.cost)
                rows.append(
                    DecisionMeasurement(
                        probe.case_id,
                        probe.question.purpose,
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
                        "expected": probe.expected,
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
    report = DecisionReport(
        "live" if live else "offline",
        load_suite(root).sha256,
        all(r.error is None for rows in all_rows.values() for r in rows),
        adapters,
        provenance,
        measured_at,
        observed_models,
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
    lines = [
        "# Typed decision comparison",
        "",
        f"Mode: **{report.mode}**. Dataset: `{report.dataset_sha256}`.",
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
        "Identical questions are extracted from the 30 frozen cases. Operational labels encode "
        "demonstration policy; investigation labels come from the reference analyses. ECE uses "
        "ten bins of selected-outcome probability (not provider certainty). Signal scores are "
        "thresholded at 0.5 and include unsupported-claim controls. Guard rows pair the exact "
        "states the agent sends with synthetic denials (other customer, unrelated sanctions name, "
        "disallowed tool, repeated call). These are narrow, correlated "
        "synthetic examples pending independent human review.",
        "",
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
        description="Compare typed decisions on frozen fictional cases."
    )
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument("--output", type=Path, default=Path("build/evals"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=PROVIDERS,
        default=list(PROVIDERS),
        help="Adapters to measure; only selected remote providers require credentials.",
    )
    parser.add_argument("--max-cost", type=Decimal, default=Decimal("1"))
    parser.add_argument("--max-tokens", type=int, default=2_500_000)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            benchmark(
                args.dataset,
                args.output,
                live=args.live,
                providers=tuple(args.providers),
                max_cost=args.max_cost,
                max_tokens=args.max_tokens,
            )
        )
    except (OSError, ValueError) as error:
        parser.exit(2, f"Decision comparison configuration failed ({type(error).__name__}).\n")
    print(f"{'PASS' if result.passed else 'FAIL'}: typed decision comparison ({result.mode}).")
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
