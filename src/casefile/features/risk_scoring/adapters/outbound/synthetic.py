"""Seeded synthetic labels, independent of the fitted model and golden cases."""

from dataclasses import dataclass
from math import exp, log1p
from random import Random
from typing import Literal

from casefile.features.risk_scoring.domain.risk import RiskInput
from casefile.shared.result import Ok


@dataclass(frozen=True, slots=True)
class SyntheticRow:
    id: str
    split: Literal["train", "validation", "test"]
    features: RiskInput
    label: int


def generate(count: int = 6000, *, seed: int = 20261024) -> tuple[SyntheticRow, ...]:
    if count < 10:
        raise ValueError("At least ten synthetic rows are required.")
    random = Random(seed)
    assignments: list[Literal["train", "validation", "test"]] = []
    assignments.extend("train" for _ in range(count * 3 // 5))
    assignments.extend("validation" for _ in range(count // 5))
    assignments.extend("test" for _ in range(count - len(assignments)))
    random.shuffle(assignments)
    rows = []
    for index, split in enumerate(assignments):
        ratio = min(100_000, exp(random.gauss(-0.5, 1.3)))
        new, foreign, night = random.random() < 0.3, random.random() < 0.2, random.random() < 0.15
        count24, age = random.randrange(1, 21), random.randrange(0, 1001)
        features = RiskInput.create(ratio, new, foreign, night, count24, age)
        assert isinstance(features, Ok)
        logit = (
            -4
            + 1.4 * log1p(ratio)
            + new
            + 0.8 * foreign
            + 0.7 * night
            + 0.2 * log1p(count24)
            + 0.3 * age / 365
            + 1.6 * (new and foreign)
        )
        probability = 1 / (1 + exp(-logit))
        rows.append(
            SyntheticRow(
                f"synthetic-{index:06}", split, features.value, int(random.random() < probability)
            )
        )
    return tuple(rows)
