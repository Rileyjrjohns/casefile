"""Read the regression floors of the offline typed-decision benchmark."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from casefile.features.evaluation.domain.decision_metrics import DecisionThresholds
from casefile.features.evaluation.domain.decision_questions import BENCHMARK_PURPOSES

type Floor = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class DecisionThresholdDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    # Only the deterministic offline adapter can be gated; remote providers are never gated.
    adapter: Literal["local"]
    min_accuracy: dict[str, Floor]

    @field_validator("min_accuracy")
    @classmethod
    def known_purposes(cls, floors: dict[str, float]) -> dict[str, float]:
        if not floors or set(floors) - BENCHMARK_PURPOSES:
            raise ValueError("Gate at least one known decision purpose.")
        return floors

    def to_thresholds(self) -> DecisionThresholds:
        return DecisionThresholds(self.adapter, tuple(sorted(self.min_accuracy.items())))


def load_decision_thresholds(path: Path) -> DecisionThresholds:
    with path.open("rb") as source:
        return DecisionThresholdDocument.model_validate(tomllib.load(source)).to_thresholds()
