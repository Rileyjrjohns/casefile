from math import nan

import pytest

from casefile.features.risk_scoring.adapters.outbound.local import LocalRiskModel
from casefile.features.risk_scoring.adapters.outbound.synthetic import generate
from casefile.features.risk_scoring.domain.risk import RiskInput, RiskPrediction
from casefile.shared.result import Err, Ok


def test_risk_inputs_and_predictions_reject_invalid_values() -> None:
    assert isinstance(RiskInput.create(nan, True, False, False, 1, 0), Err)
    assert isinstance(RiskInput.create(-1, True, False, False, 1, 0), Err)
    assert isinstance(RiskInput.create(1, True, False, False, -1, 0), Err)
    assert isinstance(RiskPrediction.create(1.1, "risk-v1"), Err)
    assert isinstance(RiskPrediction.create(nan, "risk-v1"), Err)
    assert isinstance(RiskPrediction.create(0.5, ""), Err)
    assert isinstance(RiskPrediction.create(0.5, "risk-v1"), Ok)


def test_synthetic_dataset_is_seeded_and_splits_do_not_overlap() -> None:
    first = generate(100, seed=42)
    assert first == generate(100, seed=42)
    assert first != generate(100, seed=43)
    assert len({row.id for row in first}) == 100
    assert {row.split for row in first} == {"train", "validation", "test"}
    assert all(row.label in (0, 1) for row in first)
    with pytest.raises(ValueError):
        generate(0, seed=42)


def test_versioned_local_model_returns_a_sourced_probability() -> None:
    model = LocalRiskModel.load_default()
    sample = RiskInput.create(9, True, True, True, 8, 600)
    assert isinstance(sample, Ok)
    prediction = model.predict(sample.value)
    assert 0 <= prediction.probability <= 1
    assert prediction.version == "risk-v1"
    assert prediction.source == "risk_model@risk-v1"
    assert prediction == model.predict(sample.value)
