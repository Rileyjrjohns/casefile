"""Read validated JSON weights; inference never deserializes executable model objects."""

from importlib.resources import files
from math import exp
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.features.risk_scoring.domain.risk import FEATURES, RiskInput, RiskPrediction
from casefile.shared.result import Ok

type Finite = Annotated[float, Field(allow_inf_nan=False)]


class ModelDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    version: Literal["risk-v1"]
    features: tuple[str, ...]
    mean: tuple[Finite, ...]
    scale: tuple[Annotated[float, Field(gt=0, allow_inf_nan=False)], ...]
    hidden_weight: tuple[tuple[Finite, ...], ...]
    hidden_bias: tuple[Finite, ...]
    output_weight: tuple[Finite, ...]
    output_bias: Finite
    temperature: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if (
            self.features != FEATURES
            or len(self.mean) != len(FEATURES)
            or len(self.scale) != len(FEATURES)
            or not 1 <= len(self.hidden_bias) <= 64
            or len(self.hidden_weight) != len(self.hidden_bias)
            or len(self.output_weight) != len(self.hidden_bias)
            or any(len(row) != len(FEATURES) for row in self.hidden_weight)
        ):
            raise ValueError("Model architecture or feature schema is inconsistent.")
        return self


class LocalRiskModel:
    def __init__(self, model: ModelDocument) -> None:
        self.model = model

    @classmethod
    def load(cls, path: Path) -> "LocalRiskModel":
        if path.stat().st_size > 100_000:
            raise ValueError("Risk model exceeds the allowed size.")
        return cls(ModelDocument.model_validate_json(path.read_text()))

    @classmethod
    def load_default(cls) -> "LocalRiskModel":
        packaged = files("casefile").joinpath("models/risk-v1.json")
        if packaged.is_file():
            return cls(ModelDocument.model_validate_json(packaged.read_text()))
        return cls.load(Path(__file__).resolve().parents[6] / "models/risk-v1.json")

    def predict(self, features: RiskInput) -> RiskPrediction:
        model = self.model
        scaled = tuple(
            (value - mean) / scale
            for value, mean, scale in zip(features.vector(), model.mean, model.scale, strict=True)
        )
        hidden = tuple(
            max(0.0, sum(weight * value for weight, value in zip(row, scaled, strict=True)) + bias)
            for row, bias in zip(model.hidden_weight, model.hidden_bias, strict=True)
        )
        logit = (
            sum(weight * value for weight, value in zip(model.output_weight, hidden, strict=True))
            + model.output_bias
        ) / model.temperature
        probability = 1 / (1 + exp(-max(-700, min(700, logit))))
        result = RiskPrediction.create(probability, model.version)
        assert isinstance(result, Ok)
        return result.value
