import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from casefile.bootstrap.evaluations import AgentCaseRunner, run_evaluations
from casefile.features.evaluation.adapters.outbound.golden import (
    GoldenDocument,
    ThresholdDocument,
    load_suite,
    load_thresholds,
    read_fixture,
)
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.http_model import (
    HttpLanguageModel,
    ModelSettings,
)
from tests.test_evaluations import EVALS


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first", [False, True])
async def test_http_evaluations_report_usage_and_continue_after_provider_failure(
    tmp_path: Path, fail_first: bool
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if fail_first and calls == 1:
            raise httpx.ConnectError("provider included test-secret", request=request)
        body = json.loads(request.content)
        narrative = json.loads(body["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "model": "simulated-http-v1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Fictional evidence requires human review.",
                                    "statements": [
                                        dict(
                                            key=signal["key"],
                                            text=signal["statement"],
                                            sources=signal["sources"],
                                        )
                                        for signal in narrative["signals"]
                                    ],
                                }
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    settings = ModelSettings(
        "https://model.example/chat/completions",
        "simulated-http-v1",
        "test-secret",
        Decimal(1),
        Decimal(2),
    )
    before = load_suite(EVALS).sha256
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await run_evaluations(
            EVALS, tmp_path, model=HttpLanguageModel(client, settings, AgentJsonCodec())
        )
    assert calls == 30
    assert report.mode == "http" and report.passed is not fail_first
    assert report.cases[-1].model == "simulated-http-v1"
    assert report.cases[-1].estimated_cost == Decimal("0.00014")
    assert report.cases[-1].tokens == 120
    assert report.cases[-1].summary
    assert load_suite(EVALS).sha256 == before
    assert "test-secret" not in (tmp_path / "report.json").read_text()
    if fail_first:
        assert report.cases[0].error == "ConnectError"
        assert report.cases[0].estimated_cost > 0  # Unknown usage retains its reservation.
        assert report.metrics.completion_rate == 29 / 30
    else:
        assert report.metrics.estimated_cost == Decimal("0.00420")


@pytest.mark.parametrize("defect", ["version", "fictional", "duplicates", "sources", "extra"])
def test_reference_schema_rejects_ambiguous_documents(defect: str) -> None:
    document = json.loads((EVALS / "golden/case-005.json").read_text())
    match defect:
        case "version":
            document["schema_version"] = 2
        case "fictional":
            document["fictional"] = False
        case "duplicates":
            document["expected"]["signals"] *= 2
        case "sources":
            document["expected"]["signals"][0]["sources"] *= 2
        case "extra":
            document["unrecognized"] = True
    with pytest.raises(ValidationError):
        GoldenDocument.model_validate(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_invalid_source_rate", 0.01),
        ("min_cases", 29),
        ("min_signal_recall", float("nan")),
        ("max_latency_ms", 0),
        ("max_cost_per_case", "NaN"),
        ("min_signal_precision", 1.01),
        ("unexpected", 1),
    ],
)
def test_threshold_schema_fails_closed(field: str, value: object) -> None:
    limits = load_thresholds(EVALS / "thresholds.toml")
    document: dict[str, object] = {
        "schema_version": 1,
        "min_cases": limits.min_cases,
        "min_signal_precision": limits.min_signal_precision,
        "min_signal_recall": limits.min_signal_recall,
        "min_recommendation_accuracy": limits.min_recommendation_accuracy,
        "max_invalid_source_rate": 0.0,
        "max_cost_per_case": "0.05",
        "max_latency_ms": 30000,
    }
    document[field] = value
    with pytest.raises(ValidationError):
        ThresholdDocument.model_validate(document)


def test_loader_checks_file_identity_and_size(tmp_path: Path) -> None:
    shutil.copytree(EVALS, tmp_path / "evals")
    path = tmp_path / "evals/golden/case-001.json"
    document = json.loads(path.read_text())
    document["id"] = "case-099"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="identity"):
        load_suite(tmp_path / "evals")
    path.write_text(" " * 1_000_001)
    with pytest.raises(ValueError, match="limit"):
        read_fixture(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["duplicate_tool", "unknown_reference", "bad_evidence"])
async def test_invalid_scenarios_are_failed_observations(defect: str) -> None:
    case = load_suite(EVALS).cases[4]
    scenario, replay = json.loads(case.scenario_json), json.loads(case.replay_json)
    if defect == "duplicate_tool":
        scenario["evidence"].append(scenario["evidence"][0])
        replay["request"]["evidence"] = scenario["evidence"]
        case = replace(case, scenario_json=json.dumps(scenario), replay_json=json.dumps(replay))
    elif defect == "unknown_reference":
        signal = case.expected.signals[0]
        source = replace(signal.sources[0], record_id="absent")
        case = replace(
            case, expected=replace(case.expected, signals=(replace(signal, sources=(source,)),))
        )
    else:
        scenario["evidence"][1]["records"][0]["amount"] = "not money"
        case = replace(case, scenario_json=json.dumps(scenario))
    observation = await AgentCaseRunner().run(case)
    assert observation.state == "ERROR" and observation.error is not None


@pytest.mark.parametrize("mode", ["replay", "http"])
def test_cli_ignores_model_configuration_in_replay_and_requires_it_for_http(
    tmp_path: Path, mode: str
) -> None:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("CASEFILE_")
    }
    environment["CASEFILE_MODEL_URL"] = "invalid-test-secret"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "casefile.bootstrap.evaluations",
            "--mode",
            mode,
            "--dataset",
            str(EVALS),
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
        check=False,
    )
    assert result.returncode == (0 if mode == "replay" else 2)
    assert "invalid-test-secret" not in result.stdout + result.stderr
    if mode == "replay":
        assert "PASS: 30 cases" in result.stdout


def test_cli_regression_exits_nonzero_and_keeps_reports(tmp_path: Path) -> None:
    dataset = tmp_path / "evals"
    shutil.copytree(EVALS, dataset)
    path = dataset / "replay/case-005.json"
    replay = json.loads(path.read_text())
    replay["response"]["statements"] = []
    path.write_text(json.dumps(replay))
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
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert "FAIL: 30 cases" in result.stdout
    assert json.loads((output / "report.json").read_text())["passed"] is False
