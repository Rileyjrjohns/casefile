from decimal import Decimal

import pytest

from casefile.features.evaluation.domain.decision_metrics import DecisionMeasurement, summarize


def test_calibration_uses_selected_probability_and_counts_failures() -> None:
    rows = (
        DecisionMeasurement("a", "guard", True, 0.8, 10, Decimal(".01")),
        DecisionMeasurement("b", "guard", False, 0.8, 30, Decimal(".02")),
    )
    metrics = summarize(rows)
    assert metrics["accuracy"] == 0.5
    assert metrics["ece"] == pytest.approx(0.3)
    assert metrics["latency_p50_ms"] == 10
    assert metrics["latency_p95_ms"] == 30
    assert metrics["cost_per_case"] == "0.015"
    with pytest.raises(ValueError):
        summarize(())
