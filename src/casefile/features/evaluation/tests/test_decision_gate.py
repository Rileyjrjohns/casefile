from pathlib import Path

import pytest

from casefile.features.evaluation.adapters.outbound.decision_thresholds import (
    load_decision_thresholds,
)
from casefile.features.evaluation.domain.decision_metrics import (
    DecisionThresholds,
    accuracy_violations,
)

FLOORS = DecisionThresholds("local", (("priority", 0.9), ("signal", 0.5)))


def test_accuracy_at_or_above_every_floor_passes() -> None:
    assert accuracy_violations({"priority": 0.9, "signal": 0.75, "guard": 0.0}, FLOORS) == ()


def test_a_regressed_purpose_is_reported_with_its_floor() -> None:
    (violation,) = accuracy_violations({"priority": 0.8999, "signal": 1.0}, FLOORS)
    assert "local" in violation and "priority" in violation
    assert "0.900" in violation


def test_a_gated_purpose_that_was_not_measured_is_a_violation() -> None:
    (violation,) = accuracy_violations({"priority": 1.0}, FLOORS)
    assert "signal" in violation and "not measured" in violation


def test_ungated_purposes_never_fail() -> None:
    assert accuracy_violations({"priority": 1.0, "signal": 1.0, "alert_type": 0.0}, FLOORS) == ()


def test_floors_are_read_from_a_strict_toml_document(tmp_path: Path) -> None:
    path = tmp_path / "decision-thresholds.toml"
    path.write_text('schema_version = 1\nadapter = "local"\n[min_accuracy]\nguard = 0.75\n')
    assert load_decision_thresholds(path) == DecisionThresholds("local", (("guard", 0.75),))


@pytest.mark.parametrize(
    "document",
    [
        'schema_version = 2\nadapter = "local"\n[min_accuracy]\nguard = 0.5\n',
        'schema_version = 1\nadapter = "local"\n[min_accuracy]\nguard = 1.5\n',
        'schema_version = 1\nadapter = "local"\n[min_accuracy]\nunknown = 0.5\n',
        'schema_version = 1\nadapter = "local"\n[min_accuracy]\n',
        'schema_version = 1\nadapter = "jev"\n[min_accuracy]\nguard = 0.5\n',
        'schema_version = 1\nadapter = "local"\nextra = 1\n[min_accuracy]\nguard = 0.5\n',
    ],
)
def test_invalid_threshold_documents_are_rejected(tmp_path: Path, document: str) -> None:
    path = tmp_path / "decision-thresholds.toml"
    path.write_text(document)
    with pytest.raises(ValueError):
        load_decision_thresholds(path)
