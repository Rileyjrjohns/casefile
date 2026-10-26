from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationErrorCode
from casefile.features.investigation.tests.factories import error_code, value


def usage(calls: int, tokens: int, cost: str) -> BudgetUsage:
    return value(BudgetUsage.create(calls, tokens, Decimal(cost)))


def exact(left: Decimal, right: Decimal, *, subtract: bool = False) -> Decimal:
    """Reference arithmetic with enough precision that nothing can be rounded."""
    with localcontext() as context:
        context.prec = 2000
        return left - right if subtract else left + right


@pytest.mark.parametrize(
    ("released", "code"),
    [
        (usage(4, 0, "0"), InvestigationErrorCode.INVALID_USAGE),
        (usage(0, 11, "0"), InvestigationErrorCode.INVALID_USAGE),
        (usage(0, 0, "0.51"), InvestigationErrorCode.INVALID_USAGE),
        (usage(4, 11, "0.51"), InvestigationErrorCode.INVALID_USAGE),
    ],
    ids=["tool_calls", "tokens", "estimated_cost", "all"],
)
def test_releasing_more_than_reserved_on_any_single_dimension_is_rejected(
    released: BudgetUsage, code: InvestigationErrorCode
) -> None:
    held = usage(3, 10, "0.50")
    assert error_code(held.minus(released)) == code


@pytest.mark.parametrize(
    ("released", "remaining"),
    [
        (usage(3, 10, "0.50"), usage(0, 0, "0")),
        (usage(1, 0, "0"), usage(2, 10, "0.50")),
        (usage(0, 4, "0"), usage(3, 6, "0.50")),
        (usage(0, 0, "0.20"), usage(3, 10, "0.30")),
        (BudgetUsage.zero(), usage(3, 10, "0.50")),
    ],
)
def test_release_subtracts_each_dimension_independently(
    released: BudgetUsage, remaining: BudgetUsage
) -> None:
    result = value(usage(3, 10, "0.50").minus(released))
    assert (result.tool_calls, result.tokens) == (remaining.tool_calls, remaining.tokens)
    assert result.estimated_cost == remaining.estimated_cost


def test_plus_adds_each_dimension_independently() -> None:
    total = usage(1, 20, "0.10").plus(usage(2, 5, "0.25"))
    assert (total.tool_calls, total.tokens, total.estimated_cost) == (3, 25, Decimal("0.35"))


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1E+30", "1E-30"),
        ("1E-30", "1E+30"),
        ("123456789012345678901234567890", "0.000000000000000000000000000001"),
        ("1E-40", "0"),
        ("1E+60", "1E-60"),
    ],
)
def test_costs_with_different_exponents_are_added_without_rounding(left: str, right: str) -> None:
    total = usage(0, 0, left).plus(usage(0, 0, right)).estimated_cost
    assert total == exact(Decimal(left), Decimal(right))
    assert total != Decimal(left) or Decimal(right) == 0


@pytest.mark.parametrize(
    ("left", "right"), [("1E+30", "1E-30"), ("1E+60", "1E-60"), ("1.5", "0.0000000000000000000001")]
)
def test_costs_with_different_exponents_are_subtracted_without_rounding(
    left: str, right: str
) -> None:
    held = usage(0, 0, left).plus(usage(0, 0, right))
    remaining = value(held.minus(usage(0, 0, right)))
    assert remaining.estimated_cost == Decimal(left)
    back = value(held.minus(usage(0, 0, left)))
    assert back.estimated_cost == Decimal(right)


cost_strategy = st.decimals(
    min_value=0, max_value=Decimal("1E+40"), allow_nan=False, allow_infinity=False, places=None
)


@given(left=cost_strategy, right=cost_strategy)
def test_cost_arithmetic_is_exact_for_arbitrary_exponents(left: Decimal, right: Decimal) -> None:
    first = value(BudgetUsage.create(0, 0, left))
    second = value(BudgetUsage.create(0, 0, right))
    total = first.plus(second)
    assert total.estimated_cost == exact(left, right)
    assert value(total.minus(second)).estimated_cost == left
    assert value(total.minus(first)).estimated_cost == right


@given(calls=st.integers(0, 9), tokens=st.integers(0, 99), cost=st.integers(0, 99))
def test_only_the_exceeded_dimensions_are_reported(calls: int, tokens: int, cost: int) -> None:
    limits = value(BudgetLimits.create(5, 50, Decimal("0.50")))
    reported = usage(calls, tokens, str(Decimal(cost) / 100)).exceeded_dimensions(limits)
    assert reported == tuple(
        name
        for name, over in (
            ("tool_calls", calls > 5),
            ("tokens", tokens > 50),
            ("estimated_cost", cost > 50),
        )
        if over
    )
