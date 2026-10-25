import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from torch import nn

from casefile.features.risk_scoring.adapters.outbound.local import LocalRiskModel, ModelDocument
from casefile.features.risk_scoring.adapters.outbound.synthetic import generate
from casefile.features.risk_scoring.adapters.outbound.training import metrics, train

ROOT = Path(__file__).resolve().parents[1]


def test_portable_inference_matches_pytorch_checkpoint_and_dataset_hash() -> None:
    model = LocalRiskModel.load_default()
    network = nn.Sequential(nn.Linear(6, 12), nn.ReLU(), nn.Linear(12, 1)).double()
    network.load_state_dict(torch.load(ROOT / "models/risk-v1.pt", weights_only=True))
    network.eval()
    rows = generate(100)
    scaled = np.array(
        [
            (np.array(row.features.vector()) - np.array(model.model.mean))
            / np.array(model.model.scale)
            for row in rows
        ]
    )
    with torch.no_grad():
        expected = (
            torch.sigmoid(network(torch.tensor(scaled)) / model.model.temperature)
            .numpy()
            .reshape(-1)
        )
    actual = np.array([model.predict(row.features).probability for row in rows])
    assert np.allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert (
        model.model.dataset_sha256
        == hashlib.sha256((ROOT / "data/synthetic/risk-v1.csv").read_bytes()).hexdigest()
    )
    report = json.loads((ROOT / "models/risk-v1.metrics.json").read_text())
    assert report["pytorch"]["auc_pr"] > report["positive_rate_test"] + 0.1


def test_training_uses_only_train_statistics_and_exports_held_out_metrics(tmp_path: Path) -> None:
    exported = train(tmp_path / "models", tmp_path / "dataset.csv", count=500, seed=42, epochs=5)
    rows = generate(500, seed=42)
    training = np.array([row.features.vector() for row in rows if row.split == "train"])
    assert np.allclose(training.mean(axis=0), exported.mean)
    assert np.allclose(training.std(axis=0), exported.scale)
    report = json.loads((tmp_path / "models/risk-v1.metrics.json").read_text())
    assert report["split_sizes"] == {"train": 300, "validation": 100, "test": 100}
    assert 0 <= report["pytorch"]["ece_10_bins"] <= 1
    assert 0 <= report["logistic_regression"]["auc_pr"] <= 1
    with pytest.raises(ValueError):
        train(tmp_path, tmp_path / "unused.csv", epochs=0)


def test_model_rejects_unknown_features_nonfinite_weights_and_bad_shapes(tmp_path: Path) -> None:
    baseline = LocalRiskModel.load_default().model.model_dump()
    for field, value in (
        ("features", ["unrecognized"]),
        ("hidden_bias", []),
        ("temperature", 0),
        ("output_bias", float("nan")),
    ):
        with pytest.raises(ValueError):
            ModelDocument.model_validate({**baseline, field: value})
    path = tmp_path / "too-large.json"
    path.write_text(" " * 100001)
    with pytest.raises(ValueError):
        LocalRiskModel.load(path)


def test_metrics_handle_certain_probabilities_and_empty_bins() -> None:
    result = metrics(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    assert result["ece_10_bins"] == result["brier"] == 0
    assert result["auc_pr"] == 1
