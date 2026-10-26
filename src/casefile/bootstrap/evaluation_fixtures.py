"""Record replay fixtures for a reference suite from the deterministic fake narrative model.

The recorded exchange is the model request the agent builds from the case's tool snapshots and the
fake model's reply to it. Nothing is called over the network and no expected label is read: a
fixture only pins which narrative request is legitimate for the evidence, so a later change to the
evidence, the approved signals or the prompt cannot silently reuse an old reply.
"""

import argparse
import asyncio
import json
from pathlib import Path

from casefile.bootstrap.evaluations import (
    CURRENT_PROFILE,
    AgentCaseRunner,
    ScenarioDocument,
    tool_recordings,
)
from casefile.features.evaluation.adapters.outbound.golden import GoldenDocument
from casefile.features.evaluation.application.benchmark import BenchmarkCase
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    ModelRequest,
    ModelResponse,
    TraceEvent,
)
from casefile.features.investigation.domain.budget import BudgetUsage


class RecordingLanguageModel:
    """The fake narrative model, remembering every exchange it served."""

    def __init__(self) -> None:
        self.inner = FakeLanguageModel()
        self.exchanges: list[tuple[ModelRequest, ModelResponse, BudgetUsage]] = []

    def quote(self, request: ModelRequest) -> BudgetUsage:
        return self.inner.quote(request)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await self.inner.generate(request)
        self.exchanges.append((request, response, self.inner.quote(request)))
        return response


async def record_replay(case: BenchmarkCase, findings_policy: str) -> str:
    """Run the case under `findings_policy` and return its replay document as JSON text."""
    scenario = ScenarioDocument.model_validate_json(case.scenario_json)
    recorder = RecordingLanguageModel()

    def load(codec: AgentJsonCodec) -> tuple[CaseContext, tuple[TraceEvent, ...]]:
        context, _, events = tool_recordings(scenario, codec)
        return context, tuple(events)

    observation = await AgentCaseRunner(recorder, findings_policy=findings_policy).observe(
        case.id, load
    )
    if len(recorder.exchanges) != 1 or observation.state != "AWAITING_REVIEW":
        raise ValueError(
            f"Cannot record {case.id}: the run ended in {observation.state} "
            f"({observation.error}) without one accepted narrative exchange."
        )
    request, response, reservation = recorder.exchanges[0]
    codec = AgentJsonCodec()
    document = {
        "schema_version": 1,
        "provenance": "synthetic-fixture",
        "request": json.loads(codec.request_json(request)),
        "response": json.loads(codec.response_json(response)),
        "reservation": {
            "tool_calls": reservation.tool_calls,
            "tokens": reservation.tokens,
            "estimated_cost": str(reservation.estimated_cost),
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def golden_case(path: Path) -> BenchmarkCase:
    document = GoldenDocument.model_validate_json(path.read_text(encoding="utf-8"))
    if document.id != path.stem:
        raise ValueError("Golden case identity must match its filename.")
    scenario = json.dumps({"context": document.context, "evidence": document.evidence})
    return BenchmarkCase(
        document.id,
        document.category,
        document.rationale,
        document.expected.to_expected(),
        scenario,
        "",
    )


async def write_missing(root: Path, findings_policy: str) -> tuple[Path, ...]:
    """Record the replay of each golden case that has none; existing fixtures are never touched."""
    written: list[Path] = []
    for golden in sorted((root / "golden").glob("*.json")):
        target = root / "replay" / golden.name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            await record_replay(golden_case(golden), findings_policy), encoding="utf-8"
        )
        written.append(target)
    return tuple(written)


async def drifted(root: Path, findings_policy: str) -> tuple[Path, ...]:
    """Replay fixtures that differ from what the fake narrative model would record today."""
    stale: list[Path] = []
    for golden in sorted((root / "golden").glob("*.json")):
        target = root / "replay" / golden.name
        recorded = await record_replay(golden_case(golden), findings_policy)
        if target.read_text(encoding="utf-8") != recorded:
            stale.append(target)
    return tuple(stale)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record missing replay fixtures for the current-policy suite (fake model)."
    )
    parser.add_argument("--dataset", type=Path, default=Path("evals"))
    parser.add_argument(
        "--check", action="store_true", help="Fail when a fixture differs from a fresh recording."
    )
    args = parser.parse_args()
    root = args.dataset / CURRENT_PROFILE.subdirectory
    policy = CURRENT_PROFILE.findings_policy
    if args.check:
        stale = asyncio.run(drifted(root, policy))
        for path in stale:
            print(f"Drifted: {path}")
        raise SystemExit(1 if stale else 0)
    for path in asyncio.run(write_missing(root, policy)):
        print(f"Recorded: {path}")


if __name__ == "__main__":
    main()
