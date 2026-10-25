"""Provider-independent questions, bounded answers and metered usage."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result

type Operation = Literal["choose", "probability", "score"]
PURPOSES = frozenset(
    {
        "priority",
        "alert_type",
        "tool_route",
        "model_route",
        "guard",
        "injection",
        "signal",
        "recommendation",
        "risk",
    }
)


@dataclass(frozen=True, slots=True)
class Usage:
    tokens: int = 0
    cost: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True, init=False)
class Question(FactoryOnly):
    operation: Operation
    purpose: str
    state_json: str
    options: tuple[str, ...]

    @classmethod
    def create(
        cls, operation: str, purpose: str, state_json: str, options: tuple[str, ...] = ()
    ) -> Result[Question, str]:
        if operation not in {"choose", "probability", "score"} or purpose not in PURPOSES:
            return Err("Unsupported decision operation or purpose.")
        try:
            state = json.loads(state_json)
            json.dumps(state, allow_nan=False)
        except (ValueError, RecursionError):
            return Err("Decision state must be a JSON object.")
        if not isinstance(state, dict) or len(state_json.encode()) > 500_000:
            return Err("Decision state must be a bounded JSON object.")
        if (
            len(set(options)) != len(options)
            or any(not v.strip() for v in options)
            or len(options) > 20
            or (operation == "choose" and not options)
            or (operation == "score" and not 2 <= len(options) <= 10)
            or (operation == "probability" and options)
        ):
            return Err("Invalid closed options or ordered rubric.")
        return Ok(
            cls._new(operation=operation, purpose=purpose, state_json=state_json, options=options)
        )


@dataclass(frozen=True, slots=True, init=False)
class Answer(FactoryOnly):
    value: str | float
    confidence: float
    probabilities: tuple[float, ...]
    model: str
    usage: Usage

    @classmethod
    def create(
        cls,
        question: Question,
        value: str | float,
        confidence: float,
        probabilities: tuple[float, ...],
        model: str,
        usage: Usage,
    ) -> Result[Answer, str]:
        if (
            not math.isfinite(confidence)
            or not 0 <= confidence <= 1
            or not model.strip()
            or usage.tokens < 0
            or not usage.cost.is_finite()
            or usage.cost < 0
        ):
            return Err("Invalid confidence, identity or usage.")
        if question.operation == "probability":
            if isinstance(value, str) or not math.isfinite(value) or not 0 <= value <= 1:
                return Err("A probability must be finite and between zero and one.")
            if probabilities:
                return Err("Binary probability carries no separate distribution.")
        else:
            if (
                len(probabilities) != len(question.options)
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)
                or not math.isclose(sum(probabilities), 1, abs_tol=1e-5)
            ):
                return Err("Distribution must cover every option and sum to one.")
            if question.operation == "choose":
                if not isinstance(value, str) or value not in question.options:
                    return Err("Choice is outside the closed options.")
                if probabilities[question.options.index(value)] + 1e-6 < max(probabilities):
                    return Err("Choice must have maximal probability.")
            elif (
                isinstance(value, str)
                or not math.isfinite(value)
                or not 0 <= value <= len(question.options) - 1
                or not math.isclose(
                    value, sum(i * p for i, p in enumerate(probabilities)), abs_tol=1e-4
                )
            ):
                return Err("Score must equal the probability-weighted rubric position.")
        return Ok(
            cls._new(
                value=value,
                confidence=confidence,
                probabilities=probabilities,
                model=model,
                usage=usage,
            )
        )
