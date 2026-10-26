import json
from dataclasses import replace
from pathlib import Path

import pytest

from casefile.bootstrap.evaluations import LEGACY_PROFILE, AgentCaseRunner, run_evaluations
from casefile.features.evaluation.adapters.outbound.golden import load_suite, load_thresholds
from casefile.features.evaluation.adapters.outbound.reports import write_report
from casefile.features.evaluation.application.benchmark import BenchmarkCase, EvaluateSuite
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    LEGACY_FINDINGS,
)

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"


@pytest.mark.asyncio
async def test_all_golden_cases_replay_with_perfect_structural_metrics(tmp_path: Path) -> None:
    report = await run_evaluations(EVALS, tmp_path, profile=LEGACY_PROFILE)
    assert report.passed, report.violations
    assert len(report.cases) == 30
    assert report.metrics.signal_precision == report.metrics.signal_recall == 1
    assert report.metrics.recommendation_accuracy == report.metrics.completion_rate == 1
    assert report.metrics.invalid_source_rate == 0
    assert report.metrics.estimated_cost == 0
    document = json.loads((tmp_path / "report.json").read_text())
    assert document["passed"] is True
    assert document["mode"] == "replay"
    assert document["findings_policy"] == LEGACY_FINDINGS
    assert document["profile"] == "findings-v1 regression profile"
    assert len(document["cases"]) == 30
    markdown = (tmp_path / "report.md").read_text()
    assert "synthetic" in markdown and "latency" in markdown
    assert "Profile: **findings-v1 regression profile**" in markdown
    assert all(case.case_id in markdown for case in report.cases)


@pytest.mark.asyncio
async def test_replay_request_drift_fails_without_rewriting_references() -> None:
    suite = load_suite(EVALS)
    case = suite.cases[0]
    scenario = json.loads(case.scenario_json)
    scenario["context"]["description"] = "Different evidence request"
    altered = replace(case, scenario_json=json.dumps(scenario))
    result = await AgentCaseRunner(findings_policy=LEGACY_FINDINGS).run(altered)
    assert result.state != "AWAITING_REVIEW"
    assert result.error is not None


@pytest.mark.asyncio
async def test_corrupted_replay_citation_is_rejected_and_reported(tmp_path: Path) -> None:
    suite = load_suite(EVALS)
    case = next(case for case in suite.cases if case.expected.signals)
    replay = json.loads(case.replay_json)
    replay["response"]["statements"][0]["sources"][0]["record_id"] = "invented"
    altered = replace(case, replay_json=json.dumps(replay))
    report = await EvaluateSuite(AgentCaseRunner(findings_policy=LEGACY_FINDINGS)).execute(
        (altered,),
        replace(load_thresholds(EVALS / "thresholds.toml"), min_cases=1),
        mode="replay",
        dataset_sha256=suite.sha256,
        profile=LEGACY_PROFILE.label,
        findings_policy=LEGACY_FINDINGS,
    )
    assert not report.passed
    assert report.metrics.completion_rate == report.metrics.signal_recall == 0
    assert report.cases[0].error
    write_report(report, tmp_path)
    assert json.loads((tmp_path / "report.json").read_text())["passed"] is False


def test_golden_cases_have_diverse_annotations_and_no_automatic_regeneration() -> None:
    suite = load_suite(EVALS)
    assert len({case.id for case in suite.cases}) == 30
    assert len({case.category for case in suite.cases}) >= 6
    assert {case.expected.recommendation for case in suite.cases} == {
        "close",
        "escalate",
        "request_information",
    }
    assert len(suite.sha256) == 64


def test_empty_or_incomplete_dataset_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="30"):
        load_suite(tmp_path)


@pytest.mark.asyncio
async def test_report_does_not_show_perfect_scores_for_runs_that_did_not_complete(
    tmp_path: Path,
) -> None:
    suite = load_suite(EVALS)
    clear = next(case for case in suite.cases if not case.expected.signals)
    scenario = json.loads(clear.scenario_json)
    scenario["context"]["description"] = "Different evidence request"
    broken = replace(clear, scenario_json=json.dumps(scenario))
    report = await EvaluateSuite(AgentCaseRunner(findings_policy=LEGACY_FINDINGS)).execute(
        (broken,),
        replace(load_thresholds(EVALS / "thresholds.toml"), min_cases=1),
        mode="replay",
        dataset_sha256=suite.sha256,
        profile=LEGACY_PROFILE.label,
        findings_policy=LEGACY_FINDINGS,
    )
    score = report.cases[0]
    assert score.state != "AWAITING_REVIEW" and score.error
    assert score.precision is None and score.recall is None
    write_report(report, tmp_path)
    row = next(
        line for line in (tmp_path / "report.md").read_text().splitlines() if clear.id in line
    )
    assert "1.000" not in row and "| — | — |" in row
    assert json.loads((tmp_path / "report.json").read_text())["cases"][0]["precision"] is None


def case_by_id(case_id: str) -> BenchmarkCase:
    return next(case for case in load_suite(EVALS).cases if case.id == case_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy,monthly_signal",
    [(LEGACY_FINDINGS, False), (CURRENT_FINDINGS, True)],
)
async def test_runner_applies_the_requested_findings_policy(
    policy: str, monthly_signal: bool
) -> None:
    # Case 009 holds two transfers that only exceed the declared volume together.
    observed = await AgentCaseRunner(FakeLanguageModel(), findings_policy=policy).run(
        case_by_id("case-009")
    )
    assert observed.state == "AWAITING_REVIEW", observed.error
    keys = {signal.key for signal in observed.signals}
    assert ("monthly_volume:2026-10:EUR" in keys) is monthly_signal
    assert observed.recommendation == ("escalate" if monthly_signal else "close")


def test_runner_has_no_hidden_findings_policy() -> None:
    assert AgentCaseRunner().findings_policy == CURRENT_FINDINGS
    with pytest.raises(ValueError, match="findings policy"):
        AgentCaseRunner(findings_policy="findings-v0")
