"""Investigation-owned decision boundary; composition maps it to other features."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from casefile.features.investigation.domain.budget import BudgetUsage


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    key: str
    operation: Literal["choose", "probability", "score"]
    purpose: str
    state_json: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionResponse:
    value: str | float
    confidence: float
    probabilities: tuple[float, ...]
    model: str
    tokens: int
    cost: Decimal


class DecisionGateway(Protocol):
    def quote(self, request: DecisionRequest) -> BudgetUsage: ...
    async def execute(self, request: DecisionRequest) -> DecisionResponse: ...


def state_json(state: Mapping[str, object]) -> str:
    return json.dumps(dict(state), sort_keys=True, separators=(",", ":"), allow_nan=False)
