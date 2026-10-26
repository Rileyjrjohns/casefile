import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from casefile.bootstrap.decision_benchmark import Probe, benchmark, main
from casefile.bootstrap.evaluations import CURRENT_PROFILE, LEGACY_PROFILE, AgentCaseRunner
from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel, answer
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.investigation.application.guard_policy import guard_denial_reasons
from tests.test_agent_guard import RecordingDecisions

ROOT = Path("evals")
PROFILES = (LEGACY_PROFILE, CURRENT_PROFILE)


@pytest.fixture
def probes(recorded_probes: tuple[Probe, ...]) -> tuple[Probe, ...]:
    return recorded_probes


def test_questions_come_from_both_suites_and_every_benchmark_purpose(
    probes: tuple[Probe, ...],
) -> None:
    assert Counter(p.suite for p in probes if p.question.purpose == "recommendation") == {
        "legacy": 30,
        "current": 69,
    }
    assert {p.question.purpose for p in probes} == {
        "priority",
        "alert_type",
        "tool_route",
        "guard",
        "model_route",
        "injection",
        "signal",
        "recommendation",
    }
    assert {p.case_id for p in probes if p.suite == "current"} == {
        case.id for case in load_suite(ROOT / "current").cases
    }


@pytest.mark.asyncio
async def test_recorded_questions_are_exactly_what_the_agent_sent(
    probes: tuple[Probe, ...],
) -> None:
    for profile in PROFILES:
        for case in load_suite(ROOT / profile.subdirectory).cases:
            recorder = RecordingDecisions()
            await AgentCaseRunner(decisions=recorder, findings_policy=profile.findings_policy).run(
                case
            )
            sent = sorted(
                (r.purpose, r.state_json, r.options)
                for r in recorder.requests
                if r.purpose != "risk"
            )
            measured = sorted(
                (p.question.purpose, p.question.state_json, p.question.options)
                for p in probes
                if p.case_id == case.id and p.origin == "recorded"
            )
            assert measured == sent, case.id


def test_the_production_recommendation_prompt_inputs_are_measured(
    probes: tuple[Probe, ...],
) -> None:
    states = [
        json.loads(p.question.state_json)
        for p in probes
        if p.question.purpose == "recommendation" and p.suite == "current"
    ]
    assert len(states) == 69
    assert all({"signals", "workflow", "evidence_gaps", "signal_count"} <= set(s) for s in states)
    assert any(s["evidence_gaps"] for s in states)
    assert any(s["signals"] for s in states)


def test_labels_declare_how_independent_of_the_policy_they_are(
    probes: tuple[Probe, ...],
) -> None:
    bases: dict[str, set[str]] = {}
    for p in probes:
        bases.setdefault(p.question.purpose, set()).add(p.basis)
    assert bases["recommendation"] == {"reference"}
    assert bases["injection"] == {"reference"}
    assert bases["signal"] == {"reference"}
    assert bases["priority"] == {"documented-rule"}
    assert bases["tool_route"] == {"documented-rule"}
    assert bases["alert_type"] == {"consistency"}
    assert bases["guard"] == {"consistency"}


def test_guard_probes_label_both_outcomes_and_the_negatives_are_realistic_denials(
    probes: tuple[Probe, ...],
) -> None:
    guards = [p for p in probes if p.question.purpose == "guard"]
    assert {p.expected for p in guards} == {True, False}
    assert all(p.origin == "recorded" for p in guards if p.expected is True)
    negatives = [p for p in guards if p.expected is False]
    assert len({p.case_id for p in negatives}) == 99
    for case_id in {p.case_id for p in negatives}:
        states = [json.loads(p.question.state_json) for p in negatives if p.case_id == case_id]
        assert len(states) == 4
        assert len({json.dumps(state, sort_keys=True) for state in states}) == 4
        assert all(guard_denial_reasons(state) for state in states)
        reasons = {reason for state in states for reason in guard_denial_reasons(state)}
        assert reasons == {
            "unrelated query scope",
            "tool not allowed for this workflow",
            "tool already called in this run",
        }
    assert all("authorized" not in p.question.state_json for p in negatives)


def test_each_case_has_one_unsupported_claim_control_labelled_unsupported(
    probes: tuple[Probe, ...],
) -> None:
    controls = [p for p in probes if "criminal conviction" in p.question.state_json]
    assert len(controls) == 99
    assert all(p.expected is False and p.question.purpose == "signal" for p in controls)
    assert all(p.origin == "negative-control" for p in controls)


@pytest.mark.asyncio
async def test_same_cases_run_through_all_adapters_without_network(tmp_path: Path) -> None:
    report = await benchmark(ROOT, tmp_path)
    assert report.passed is True
    assert report.gate_violations == ()
    assert set(report.adapters) == {"local", "jev", "llm"}
    assert (tmp_path / "decisions.md").is_file()
    assert report.current_dataset_sha256 != report.dataset_sha256


class Degraded:
    """An offline adapter that answers the opposite of the demonstration policy."""

    def __init__(self) -> None:
        self.policy = FakeDecisionModel()

    def quote(self, question: Question) -> Usage:
        return self.policy.quote(question)

    async def choose(self, question: Question) -> Answer:
        real = await self.policy.choose(question)
        others = [o for o in question.options if o != real.value]
        return answer(question, others[0] if others else real.value, model="degraded")

    async def probability(self, question: Question) -> Answer:
        real = await self.policy.probability(question)
        return answer(question, 1 - float(real.value), model="degraded")

    async def score(self, question: Question) -> Answer:
        real = await self.policy.score(question)
        return answer(question, len(question.options) - 1 - float(real.value), model="degraded")


@pytest.mark.asyncio
async def test_a_degraded_local_adapter_fails_the_accuracy_gate(tmp_path: Path) -> None:
    report = await benchmark(ROOT, tmp_path, providers=("local",), local=Degraded())
    assert report.passed is False
    failed = {violation.split(":")[0] for violation in report.gate_violations}
    assert {"local/guard", "local/recommendation", "local/injection"} <= failed
    assert all(row["errors"] == 0 for row in report.adapters["local"].values())
    assert "FAIL" in (tmp_path / "decisions.md").read_text()


@pytest.mark.asyncio
async def test_the_gate_names_a_purpose_that_is_not_measured(tmp_path: Path) -> None:
    thresholds = tmp_path / "floors.toml"
    thresholds.write_text('schema_version = 1\nadapter = "local"\n[min_accuracy]\nguard = 1.0\n')
    report = await benchmark(
        ROOT, tmp_path / "out", providers=("local",), local=Degraded(), thresholds=thresholds
    )
    assert report.gate_violations and report.gate_violations[0].startswith("local/guard")


def test_the_command_exits_non_zero_when_a_gated_purpose_regresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    strict = tmp_path / "strict.toml"
    strict.write_text('schema_version = 1\nadapter = "local"\n[min_accuracy]\nsignal = 1.0\n')
    argv = ["casefile-decisions", "--output", str(tmp_path / "out"), "--providers", "local"]

    def run(*extra: str) -> int:
        monkeypatch.setattr(sys, "argv", [*argv, *extra])
        with pytest.raises(SystemExit) as exit_:
            main()
        return int(exit_.value.code or 0)

    assert run() == 0
    assert run("--thresholds", str(strict)) == 1
    output = capsys.readouterr().out
    assert "PASS" in output and "FAIL" in output and "local/signal" in output
