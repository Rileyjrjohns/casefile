"""The current-policy suite is separate from the frozen findings-v1 regression profile."""

import json
import re
import subprocess
import sys
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from casefile.bootstrap.evaluation_fixtures import drifted
from casefile.bootstrap.evaluations import (
    CURRENT_PROFILE,
    LEGACY_PROFILE,
    AgentCaseRunner,
    ScenarioDocument,
    run_evaluations,
)
from casefile.features.evaluation.adapters.outbound.golden import load_suite, load_thresholds
from casefile.features.evaluation.application.benchmark import BenchmarkCase
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    LEGACY_FINDINGS,
)
from tests.test_evaluations import EVALS

ROOT = EVALS.parent
CURRENT = EVALS / "current"
SUITE = load_suite(CURRENT).cases if (CURRENT / "golden").is_dir() else ()
# Fingerprint of the frozen findings-v1 reference at the commit that introduced this suite.
LEGACY_DATASET_SHA256 = "bae2cfefa4bfecdc5abbc87901e467f59af9250b1f0a833e21fb8a81cd54d6fc"
BANKING = {"payment_fraud", "credit_document_fraud", "sanctions_identity_review"}


def context_of(case: BenchmarkCase) -> dict[str, str]:
    context = ScenarioDocument.model_validate_json(case.scenario_json).context
    return {key: str(value) for key, value in context.items()}


def kind(case: BenchmarkCase) -> str:
    return context_of(case)["kind"]


def subject(case: BenchmarkCase) -> str:
    return context_of(case)["subject_id"]


def test_the_frozen_legacy_profile_is_unchanged_and_still_labelled() -> None:
    assert load_suite(EVALS).sha256 == LEGACY_DATASET_SHA256
    assert len(list((EVALS / "golden").glob("*.json"))) == 30
    assert len(list((EVALS / "replay").glob("*.json"))) == 30
    with (EVALS / "thresholds.toml").open("rb") as source:
        assert tomllib.load(source) == {
            "schema_version": 1,
            "min_cases": 30,
            "min_signal_precision": 1.0,
            "min_signal_recall": 1.0,
            "min_recommendation_accuracy": 1.0,
            "max_invalid_source_rate": 0.0,
            "max_cost_per_case": "0.05",
            "max_latency_ms": 30000.0,
        }
    assert LEGACY_PROFILE.findings_policy == LEGACY_FINDINGS
    assert LEGACY_PROFILE.label == "findings-v1 regression profile"
    assert CURRENT_PROFILE.findings_policy == CURRENT_FINDINGS


def test_the_current_suite_is_a_separate_dataset_with_strict_thresholds() -> None:
    assert len(SUITE) >= 30
    assert not {case.id for case in SUITE} & {case.id for case in load_suite(EVALS).cases}
    limits = load_thresholds(CURRENT / "thresholds.toml")
    assert limits.min_cases == len(SUITE)
    assert (
        limits.min_signal_precision,
        limits.min_signal_recall,
        limits.min_recommendation_accuracy,
        limits.max_invalid_source_rate,
    ) == (1.0, 1.0, 1.0, 0.0)
    assert limits.max_cost_per_case == Decimal("0.05") and limits.max_latency_ms <= 30000


def test_cases_are_individually_reasoned_and_distinct() -> None:
    rationales = [case.rationale for case in SUITE]
    summaries = [case.expected.summary for case in SUITE]
    assert len(set(rationales)) == len(set(summaries)) == len(SUITE)
    assert all(len(text) > 120 for text in rationales)
    assert all(re.fullmatch(r"demo-customer-[0-9]{3}", subject(case)) for case in SUITE), (
        "subjects must be fictional demo customers"
    )
    names = json.dumps(
        [ScenarioDocument.model_validate_json(c.scenario_json).evidence for c in SUITE]
    )
    assert not re.search(r'"legal_name": "(?!Demo |Fictional )', names)
    assert not re.search(r'"record_id": "san-[0-9]+", "name": "(?!Demo |Fictional )', names)


def test_the_suite_covers_the_current_policy_surface() -> None:
    keys = {signal.key for case in SUITE for signal in case.expected.signals}
    prefixes = {key.split(":")[0] + (":" + key.split(":")[1] if ":" in key else "") for key in keys}
    for required in (
        "monthly_volume:2026-09",
        "monthly_volume:2026-10",
        "volume:txn-001",
        "gap:currency",
        "gap:unresolved_sanctions",
        "sanctions:san-001",
        "kyc:alert-001",
        "instruction:note-001",
        "instruction:doc-002",
        "instruction:doc-003",
        "gap:payment_documents",
        "gap:payment_verification",
        "finding:payment_amount",
        "finding:beneficiary_mismatch",
        "finding:supplier_denial",
        "context:confirmed_account_change",
        "gap:income_documents",
        "gap:income_verification",
        "gap:income_periods",
        "gap:income_amount",
        "finding:employer_discrepancy",
        "context:employer_confirmation",
        "gap:identity_documents",
        "gap:identity_verification",
        "gap:partial_identity",
        "finding:identity_match",
        "context:distinct_identity",
    ):
        assert required in prefixes, required
    kinds = [kind(case) for case in SUITE]
    for alert_kind in (
        "unusual_transaction",
        "sanctions_match",
        "kyc_inconsistency",
        *BANKING,
    ):
        assert kinds.count(alert_kind) >= 3, alert_kind
    assert {case.expected.recommendation for case in SUITE} == {
        "close",
        "escalate",
        "request_information",
    }
    # Consistent and inconsistent variants for each banking workflow, and quiet texts.
    for banking in sorted(BANKING):
        outcomes = {c.expected.recommendation for c in SUITE if kind(c) == banking}
        assert outcomes == {"close", "escalate", "request_information"}, banking
    quiet = [c for c in SUITE if c.category == "injection" and not c.expected.signals]
    assert len(quiet) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SUITE, ids=[case.id for case in SUITE])
async def test_each_case_matches_its_reasoned_expectation_exactly(case: BenchmarkCase) -> None:
    observed = await AgentCaseRunner(findings_policy=CURRENT_FINDINGS).run(case)
    assert observed.state == "AWAITING_REVIEW", observed.error
    assert observed.recommendation == case.expected.recommendation, case.rationale
    actual = {s.key: (frozenset(s.sources), s.statement, s.confidence) for s in observed.signals}
    expected = {
        s.key: (frozenset(s.sources), s.statement, s.confidence) for s in case.expected.signals
    }
    assert actual == expected


@pytest.mark.asyncio
async def test_the_suite_passes_its_own_gate_and_writes_a_labelled_report(tmp_path: Path) -> None:
    report = await run_evaluations(CURRENT, tmp_path, profile=CURRENT_PROFILE)
    assert report.passed, report.violations
    assert report.findings_policy == CURRENT_FINDINGS
    metrics = report.metrics
    assert metrics.cases == len(SUITE) >= 30
    assert metrics.signal_precision == metrics.signal_recall == 1
    assert metrics.recommendation_accuracy == metrics.completion_rate == 1
    assert metrics.invalid_source_rate == 0 and metrics.false_positives == 0
    assert metrics.false_negatives == 0
    markdown = (tmp_path / "report.md").read_text()
    assert "current-policy profile (findings-v2)" in markdown
    assert json.loads((tmp_path / "report.json").read_text())["findings_policy"] == "findings-v2"
    assert all(case.case_id in markdown for case in report.cases)


@pytest.mark.asyncio
async def test_the_current_suite_does_not_pass_under_the_historical_policy(
    tmp_path: Path,
) -> None:
    # The gate must be sensitive to the policy: pinning findings-v1 here has to fail it.
    report = await run_evaluations(
        CURRENT,
        tmp_path,
        profile=LEGACY_PROFILE,
    )
    assert not report.passed
    assert report.metrics.completion_rate < 1 or report.metrics.signal_recall < 1


@pytest.mark.asyncio
async def test_replay_fixtures_are_what_the_fake_narrative_model_records_today() -> None:
    assert await drifted(CURRENT, CURRENT_FINDINGS) == ()
    for path in (CURRENT / "replay").glob("*.json"):
        document = json.loads(path.read_text())
        assert document["provenance"] == "synthetic-fixture"
        assert document["response"]["model"] == "fake-narrative-v1"
    assert {p.name for p in (CURRENT / "replay").glob("*.json")} == {
        p.name for p in (CURRENT / "golden").glob("*.json")
    }


def test_cli_evaluates_both_profiles_and_keeps_their_reports_apart(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "casefile.bootstrap.evaluations", "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: findings-v1 regression profile, 30 cases" in result.stdout
    assert f"PASS: current-policy profile (findings-v2), {len(SUITE)} cases" in result.stdout
    legacy = json.loads((tmp_path / "report.json").read_text())
    current = json.loads((tmp_path / "current" / "report.json").read_text())
    assert (legacy["findings_policy"], current["findings_policy"]) == (
        LEGACY_FINDINGS,
        CURRENT_FINDINGS,
    )
    assert legacy["dataset_sha256"] == LEGACY_DATASET_SHA256 != current["dataset_sha256"]
    assert len(legacy["cases"]) == 30 and len(current["cases"]) == len(SUITE)


def test_cli_fails_when_only_the_current_profile_regresses_and_still_writes_both(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "evals"
    (dataset / "current").mkdir(parents=True)
    for name in ("golden", "replay"):
        (dataset / name).symlink_to(EVALS / name)
        (dataset / "current" / name).mkdir()
        for source in sorted((CURRENT / name).glob("*.json")):
            (dataset / "current" / name / source.name).write_bytes(source.read_bytes())
    for name in ("thresholds.toml",):
        (dataset / name).write_bytes((EVALS / name).read_bytes())
        (dataset / "current" / name).write_bytes((CURRENT / name).read_bytes())
    broken = dataset / "current/replay/current-001.json"
    document = json.loads(broken.read_text())
    document["response"]["statements"] = []
    broken.write_text(json.dumps(document))
    output = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "casefile.bootstrap.evaluations",
            "--dataset",
            str(dataset),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 1
    assert "PASS: findings-v1 regression profile" in result.stdout
    assert "FAIL: current-policy profile (findings-v2)" in result.stdout
    assert json.loads((output / "report.json").read_text())["passed"] is True
    failed = json.loads((output / "current" / "report.json").read_text())
    assert failed["passed"] is False
    first = failed["cases"][0]
    assert first["case_id"] == "current-001" and first["precision"] is None


def test_make_eval_and_ci_cover_both_report_directories() -> None:
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\neval:\n", 1)[1].split("\n\n", 1)[0]
    assert "casefile-eval" in recipe and "--profile legacy" not in recipe
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "path: build/evals/**" in workflow


def test_the_case_index_lists_every_case_of_both_suites() -> None:
    index = (EVALS / "CASES.md").read_text()
    for case in (*load_suite(EVALS).cases, *SUITE):
        assert f"[{case.id}]" in index, case.id
    assert "pending independent human review" in index
    assert "findings-v1 regression profile" in index and "current-policy profile" in index
