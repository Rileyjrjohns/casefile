"""Wire purpose-specific decisions and bridge tabular risk without feature imports."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import BaseModel, ConfigDict

from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel
from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.llm import HttpLanguageModel, LlmDecisionModel
from casefile.features.decisioning.adapters.outbound.local import LocalDecisionModel
from casefile.features.decisioning.adapters.outbound.wire import DecisionSettings
from casefile.features.decisioning.application.ports import DecisionModel
from casefile.features.decisioning.domain.decisions import PURPOSES, Question
from casefile.features.investigation.application.decisions import DecisionRequest, DecisionResponse
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.risk_scoring.adapters.outbound.local import LocalRiskModel
from casefile.features.risk_scoring.application.ports import RiskModel
from casefile.features.risk_scoring.domain.risk import RiskInput
from casefile.shared.result import Err


def require[T](result: T | Err[str]) -> T:
    if isinstance(result, Err):
        raise ValueError(result.error)
    return result


class RiskFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    amount_ratio: float
    new_counterparty: bool
    cross_border: bool
    night: bool
    transactions_24h: int
    kyc_age_days: int


@dataclass(frozen=True, slots=True)
class RiskBridge:
    model: RiskModel

    def predict(self, state_json: str) -> tuple[float, str]:
        fields = RiskFeatures.model_validate_json(state_json)
        value = require(
            RiskInput.create(
                fields.amount_ratio,
                fields.new_counterparty,
                fields.cross_border,
                fields.night,
                fields.transactions_24h,
                fields.kyc_age_days,
            )
        ).value
        prediction = self.model.predict(value)
        return prediction.probability, prediction.version


@dataclass(frozen=True, slots=True)
class DecisionBridge:
    default: DecisionModel = field(default_factory=FakeDecisionModel)
    routes: Mapping[str, DecisionModel] = field(default_factory=dict)

    def question(self, request: DecisionRequest) -> Question:
        return require(
            Question.create(request.operation, request.purpose, request.state_json, request.options)
        ).value

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        usage = self.routes.get(request.purpose, self.default).quote(self.question(request))
        result = BudgetUsage.create(0, usage.tokens, usage.cost)
        if isinstance(result, Err):
            raise ValueError("Invalid decision reservation.")
        return result.value

    async def execute(self, request: DecisionRequest) -> DecisionResponse:
        question = self.question(request)
        model = self.routes.get(request.purpose, self.default)
        operation = {
            "choose": model.choose,
            "probability": model.probability,
            "score": model.score,
        }[request.operation]
        answer = await operation(question)
        return DecisionResponse(
            answer.value,
            answer.confidence,
            answer.probabilities,
            answer.model,
            answer.usage.tokens,
            answer.usage.cost,
        )


def demo_decisions() -> DecisionBridge:
    return DecisionBridge(
        routes={"risk": LocalDecisionModel(RiskBridge(LocalRiskModel.load_default()))}
    )


def configured_decisions(env: Mapping[str, str], client: httpx.AsyncClient) -> DecisionBridge:
    routes = json.loads(env.get("CASEFILE_DECISIONS") or "{}")
    if not isinstance(routes, dict) or set(routes) - PURPOSES:
        raise ValueError("CASEFILE_DECISIONS must map known purposes to adapters.")
    if any(value not in {"fake", "local", "jev", "llm"} for value in routes.values()):
        raise ValueError("Unknown decision adapter.")
    if routes.get("risk", "local") != "local":
        raise ValueError("Runtime tabular risk must use the versioned local model.")
    local = LocalDecisionModel(RiskBridge(LocalRiskModel.load_default()))
    models: dict[str, DecisionModel] = {"fake": FakeDecisionModel(), "local": local}
    for name in ("jev", "llm"):
        if name not in routes.values():
            continue
        prefix = f"CASEFILE_{name.upper()}"
        if name == "jev" and not env.get(f"{prefix}_API_KEY", "").strip():
            raise ValueError("CASEFILE_JEV_API_KEY is required when Jev decisions are enabled.")
        try:
            settings = DecisionSettings(
                env[f"{prefix}_URL"],
                env[f"{prefix}_MODEL"],
                env.get(f"{prefix}_API_KEY", ""),
                Decimal(env[f"{prefix}_INPUT_COST_PER_MILLION"]),
                Decimal(env[f"{prefix}_OUTPUT_COST_PER_MILLION"]),
            )
        except KeyError as error:
            raise ValueError("Missing decision provider configuration.") from error
        except InvalidOperation as error:
            raise ValueError(
                "Decision token rates must be explicit decimal USD amounts."
            ) from error
        models[name] = (
            JevDecisionModel(client, settings)
            if name == "jev"
            else LlmDecisionModel(HttpLanguageModel(client, settings))
        )
    return DecisionBridge(
        routes={"risk": local, **{key: models[value] for key, value in routes.items()}}
    )
