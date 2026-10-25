"""Replay fixtures for a suite are recorded from the deterministic fake narrative model."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from casefile.bootstrap.evaluation_fixtures import drifted, record_replay, write_missing
from casefile.bootstrap.evaluations import AgentCaseRunner
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    LEGACY_FINDINGS,
)
from tests.test_evaluations import EVALS, case_by_id


@pytest.mark.asyncio
async def test_recorded_exchange_replays_exactly_under_the_policy_it_was_recorded_with() -> None:
    # Case 009 only yields a monthly-volume signal under findings-v2, so its frozen replay
    # cannot serve the current policy; a recording made under v2 must.
    case = case_by_id("case-009")
    recorded = await record_replay(case, CURRENT_FINDINGS)
    document = json.loads(recorded)
    assert document["provenance"] == "synthetic-fixture"
    assert document["response"]["model"] == "fake-narrative-v1"
    assert [s["key"] for s in document["request"]["signals"]] == ["monthly_volume:2026-10:EUR"]
    assert await record_replay(case, CURRENT_FINDINGS) == recorded
    observed = await AgentCaseRunner(findings_policy=CURRENT_FINDINGS).run(
        replace(case, replay_json=recorded)
    )
    assert observed.state == "AWAITING_REVIEW", observed.error
    assert [s.key for s in observed.signals] == ["monthly_volume:2026-10:EUR"]
    legacy = await AgentCaseRunner(findings_policy=LEGACY_FINDINGS).run(
        replace(case, replay_json=recorded)
    )
    assert legacy.state != "AWAITING_REVIEW" and legacy.error is not None


@pytest.mark.asyncio
async def test_a_case_whose_run_cannot_reach_the_model_cannot_be_recorded() -> None:
    case = case_by_id("case-001")
    scenario = json.loads(case.scenario_json)
    scenario["evidence"][1]["records"][0]["amount"] = "not money"
    with pytest.raises(ValueError, match="record"):
        await record_replay(replace(case, scenario_json=json.dumps(scenario)), CURRENT_FINDINGS)


@pytest.mark.asyncio
async def test_existing_fixtures_are_never_overwritten(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    golden.mkdir()
    source = EVALS / "golden/case-001.json"
    (golden / source.name).write_text(source.read_text())
    (tmp_path / "replay").mkdir()
    kept = tmp_path / "replay" / source.name
    kept.write_text("frozen")
    assert await write_missing(tmp_path, CURRENT_FINDINGS) == ()
    assert kept.read_text() == "frozen"
    kept.unlink()
    assert await write_missing(tmp_path, CURRENT_FINDINGS) == (kept,)
    assert json.loads(kept.read_text())["provenance"] == "synthetic-fixture"


@pytest.mark.asyncio
async def test_drift_check_reports_fixtures_that_no_longer_match_a_fresh_recording(
    tmp_path: Path,
) -> None:
    (tmp_path / "golden").mkdir()
    source = EVALS / "golden/case-001.json"
    (tmp_path / "golden" / source.name).write_text(source.read_text())
    (written,) = await write_missing(tmp_path, CURRENT_FINDINGS)
    assert await drifted(tmp_path, CURRENT_FINDINGS) == ()
    document = json.loads(written.read_text())
    document["response"]["summary"] = "Edited by hand."
    written.write_text(json.dumps(document, indent=2) + "\n")
    assert await drifted(tmp_path, CURRENT_FINDINGS) == (written,)
