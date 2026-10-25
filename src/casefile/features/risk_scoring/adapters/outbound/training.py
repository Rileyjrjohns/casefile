"""CPU-only reproducible training, held-out evaluation and portable weight export."""

import argparse
import csv
import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import sklearn
import torch
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from torch import nn

from casefile.features.risk_scoring.adapters.outbound.local import ModelDocument
from casefile.features.risk_scoring.adapters.outbound.synthetic import generate
from casefile.features.risk_scoring.domain.risk import FEATURES


def metrics(labels: NDArray[np.float64], probabilities: NDArray[np.float64]) -> dict[str, float]:
    ece = 0.0
    for index in range(10):
        mask = (probabilities >= index / 10) & (
            probabilities < (index + 1) / 10 if index < 9 else probabilities <= 1
        )
        if mask.any():
            ece += float(mask.mean()) * abs(
                float(probabilities[mask].mean()) - float(labels[mask].mean())
            )
    return {
        "auc_pr": float(average_precision_score(labels, probabilities)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "brier": float(brier_score_loss(labels, probabilities)),
        "ece_10_bins": ece,
        "accuracy_at_0_5": float(((probabilities >= 0.5) == labels).mean()),
    }


def train(
    output: Path, dataset: Path, *, count: int = 6000, seed: int = 20261024, epochs: int = 300
) -> ModelDocument:
    if epochs < 1:
        raise ValueError("Training requires at least one epoch.")
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    rows = generate(count, seed=seed)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("id", "split", *FEATURES, "label"))
    writer.writerows((row.id, row.split, *row.features.vector(), row.label) for row in rows)
    content = buffer.getvalue().encode()
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    x = np.array([row.features.vector() for row in rows], dtype=np.float64)
    y = np.array([row.label for row in rows], dtype=np.float64)
    masks = {
        split: np.array([row.split == split for row in rows])
        for split in ("train", "validation", "test")
    }
    mean, scale = x[masks["train"]].mean(axis=0), x[masks["train"]].std(axis=0)
    scale = np.maximum(scale, 1e-8)
    standardized = (x - mean) / scale
    train_x = torch.tensor(standardized[masks["train"]], dtype=torch.float64)
    train_y = torch.tensor(y[masks["train"]], dtype=torch.float64).reshape(-1, 1)
    validation_x = torch.tensor(standardized[masks["validation"]], dtype=torch.float64)
    validation_y = torch.tensor(y[masks["validation"]], dtype=torch.float64).reshape(-1, 1)
    network = nn.Sequential(nn.Linear(len(FEATURES), 12), nn.ReLU(), nn.Linear(12, 1)).double()
    optimizer = torch.optim.Adam(network.parameters(), lr=0.02, weight_decay=0.002)
    loss_function = nn.BCEWithLogitsLoss()
    best_loss, best_epoch = float("inf"), 0
    best_state = deepcopy(network.state_dict())
    for epoch in range(epochs):
        optimizer.zero_grad()
        loss = loss_function(network(train_x), train_y)
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            validation_loss = float(loss_function(network(validation_x), validation_y))
        if validation_loss < best_loss:
            best_loss, best_epoch = validation_loss, epoch + 1
            best_state = deepcopy(network.state_dict())
    network.load_state_dict(best_state)
    network.eval()
    with torch.no_grad():
        validation_logits = network(validation_x)
        temperature = min(
            (0.75, 1.0, 1.25, 1.5, 2.0),
            key=lambda candidate: float(loss_function(validation_logits / candidate, validation_y)),
        )
        test_x = torch.tensor(standardized[masks["test"]], dtype=torch.float64)
        probabilities = torch.sigmoid(network(test_x) / temperature).numpy().reshape(-1)
    baseline = LogisticRegression(max_iter=500, random_state=seed)
    baseline.fit(standardized[masks["train"]], y[masks["train"]])
    baseline_probabilities = baseline.predict_proba(standardized[masks["test"]])[:, 1]
    exported = ModelDocument.model_validate(
        {
            "schema_version": 1,
            "version": "risk-v1",
            "features": FEATURES,
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "hidden_weight": best_state["0.weight"].tolist(),
            "hidden_bias": best_state["0.bias"].tolist(),
            "output_weight": best_state["2.weight"][0].tolist(),
            "output_bias": best_state["2.bias"].item(),
            "temperature": temperature,
            "dataset_sha256": digest,
        }
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "risk-v1.json").write_text(exported.model_dump_json(indent=2) + "\n")
    torch.save(best_state, output / "risk-v1.pt")
    report = {
        "schema_version": 1,
        "model_version": "risk-v1",
        "fictional": True,
        "seed": seed,
        "dataset_sha256": digest,
        "rows": count,
        "split_sizes": {key: int(mask.sum()) for key, mask in masks.items()},
        "positive_rate_test": float(y[masks["test"]].mean()),
        "features": FEATURES,
        "normalization": "Training split only",
        "selection": "Minimum validation BCE; temperature selected on validation only",
        "architecture": [len(FEATURES), 12, 1],
        "epochs": epochs,
        "selected_epoch": best_epoch,
        "temperature": temperature,
        "pytorch": metrics(y[masks["test"]], probabilities),
        "logistic_regression": metrics(y[masks["test"]], baseline_probabilities),
        "versions": {
            "torch": torch.__version__,
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
        },
    }
    (output / "risk-v1.metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    return exported


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train a local risk model on seeded fictional transactions."
    )
    parser.add_argument("--output", type=Path, default=Path("models"))
    parser.add_argument("--dataset", type=Path, default=Path("data/synthetic/risk-v1.csv"))
    parser.add_argument("--seed", type=int, default=20261024)
    args = parser.parse_args()
    model = train(args.output, args.dataset, seed=args.seed)
    print(f"Exported {model.version}; dataset SHA-256 {model.dataset_sha256}")


if __name__ == "__main__":
    main()
