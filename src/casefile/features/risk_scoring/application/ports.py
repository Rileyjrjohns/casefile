from typing import Protocol

from casefile.features.risk_scoring.domain.risk import RiskInput, RiskPrediction


class RiskModel(Protocol):
    def predict(self, features: RiskInput) -> RiskPrediction: ...
