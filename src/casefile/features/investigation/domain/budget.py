"""Nonnegative run limits and consumption, with decimal estimated costs."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import cast

from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


def _valid(tool_calls: int, tokens: int, estimated_cost: Decimal) -> bool:
    return (
        type(tool_calls) is int
        and type(tokens) is int
        and tool_calls >= 0
        and tokens >= 0
        and estimated_cost.is_finite()
        and estimated_cost >= 0
    )


def _add_cost(left: Decimal, right: Decimal) -> Decimal:
    # Align every decimal place so a small overrun cannot be rounded away.
    lowest_exponent = min(cast(int, left.as_tuple().exponent), cast(int, right.as_tuple().exponent))
    with localcontext() as context:
        context.prec = max(
            context.prec, max(left.adjusted(), right.adjusted()) - lowest_exponent + 2
        )
        return left + right


@dataclass(frozen=True, slots=True, init=False)
class BudgetLimits(FactoryOnly):
    tool_calls: int
    tokens: int
    estimated_cost: Decimal

    @classmethod
    def create(
        cls, tool_calls: int, tokens: int, estimated_cost: Decimal
    ) -> Result["BudgetLimits", InvestigationError]:
        if not _valid(tool_calls, tokens, estimated_cost):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_BUDGET,
                    "Budget limits must be finite and nonnegative, with integer counters.",
                )
            )
        return Ok(cls._new(tool_calls=tool_calls, tokens=tokens, estimated_cost=estimated_cost))


@dataclass(frozen=True, slots=True, init=False)
class BudgetUsage(FactoryOnly):
    tool_calls: int
    tokens: int
    estimated_cost: Decimal

    @classmethod
    def create(
        cls, tool_calls: int, tokens: int, estimated_cost: Decimal
    ) -> Result["BudgetUsage", InvestigationError]:
        if not _valid(tool_calls, tokens, estimated_cost):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_USAGE,
                    "Usage must be finite and nonnegative, with integer counters.",
                )
            )
        return Ok(cls._new(tool_calls=tool_calls, tokens=tokens, estimated_cost=estimated_cost))

    @classmethod
    def zero(cls) -> "BudgetUsage":
        return cls._new(tool_calls=0, tokens=0, estimated_cost=Decimal(0))

    def plus(self, other: "BudgetUsage") -> "BudgetUsage":
        return self._new(
            tool_calls=self.tool_calls + other.tool_calls,
            tokens=self.tokens + other.tokens,
            estimated_cost=_add_cost(self.estimated_cost, other.estimated_cost),
        )

    def minus(self, other: "BudgetUsage") -> Result["BudgetUsage", InvestigationError]:
        if (
            other.tool_calls > self.tool_calls
            or other.tokens > self.tokens
            or other.estimated_cost > self.estimated_cost
        ):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_USAGE,
                    "Cannot release more usage than was reserved.",
                )
            )
        return Ok(
            self._new(
                tool_calls=self.tool_calls - other.tool_calls,
                tokens=self.tokens - other.tokens,
                estimated_cost=_add_cost(self.estimated_cost, other.estimated_cost.copy_negate()),
            )
        )

    def exceeded_dimensions(self, limits: BudgetLimits) -> tuple[str, ...]:
        return tuple(
            name
            for name, exceeded in (
                ("tool_calls", self.tool_calls > limits.tool_calls),
                ("tokens", self.tokens > limits.tokens),
                ("estimated_cost", self.estimated_cost > limits.estimated_cost),
            )
            if exceeded
        )
