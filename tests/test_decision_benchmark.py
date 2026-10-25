import json
from pathlib import Path

import pytest

from casefile.bootstrap.decision_benchmark import benchmark, probes
from casefile.bootstrap.evaluations import AgentCaseRunner
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.investigation.application.guard_policy import guard_denial_reasons
from tests.test_agent_guard import RecordingDecisions


@pytest.mark.asyncio
async def test_same_frozen_cases_run_through_all_adapters_without_network(tmp_path: Path) -> None:
    suite = probes(Path("evals"))
    assert len({p.case_id for p in suite}) == 30
    assert {p.question.purpose for p in suite} == {
        "priority",
        "alert_type",
        "tool_route",
        "guard",
        "model_route",
        "injection",
        "signal",
        "recommendation",
    }
    report = await benchmark(Path("evals"), tmp_path)
    assert report.passed is True
    assert set(report.adapters) == {"local", "jev", "llm"}
    assert (tmp_path / "decisions.md").is_file()


@pytest.mark.asyncio
async def test_guard_probes_label_both_outcomes_and_positives_are_the_agents_real_states() -> None:
    root = Path("evals")
    guards = [p for p in probes(root) if p.question.purpose == "guard"]
    assert {p.expected for p in guards} == {True, False}
    for case in load_suite(root).cases:
        recorder = RecordingDecisions()
        await AgentCaseRunner(decisions=recorder).run(case)
        sent = sorted(r.state_json for r in recorder.requests if r.purpose == "guard")
        positives = sorted(
            p.question.state_json for p in guards if p.case_id == case.id and p.expected is True
        )
        assert sent and positives == sent


def test_guard_negatives_are_realistic_denials_with_distinct_failing_facts() -> None:
    negatives = [
        p for p in probes(Path("evals")) if p.question.purpose == "guard" and p.expected is False
    ]
    assert len({p.case_id for p in negatives}) == 30
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
