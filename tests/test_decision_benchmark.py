from pathlib import Path

import pytest

from casefile.bootstrap.decision_benchmark import benchmark, probes


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
