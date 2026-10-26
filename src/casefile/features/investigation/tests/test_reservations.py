from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationErrorCode
from casefile.features.investigation.domain.run import RunState
from casefile.features.investigation.tests.factories import error_code, run_at, value
from casefile.shared.result import Ok


def test_reservation_blocks_external_work_before_exceeding_a_limit() -> None:
    run = run_at(RunState.GATHERING)
    updated = value(run.reserve(value(BudgetUsage.create(6, 0, Decimal(0)))))
    assert updated.state == RunState.FAILED
    assert updated.usage == run.usage
    assert updated.failure_reason and "tool_calls" in updated.failure_reason


def test_settlement_records_actual_usage_and_detects_provider_overrun() -> None:
    run = run_at(RunState.ANALYZING)
    reserved = value(BudgetUsage.create(0, 80, Decimal("0.80")))
    run = value(run.reserve(reserved))
    actual = value(BudgetUsage.create(0, 101, Decimal("1.01")))
    updated = value(run.settle(reserved, actual))
    assert updated.state == RunState.FAILED
    assert updated.usage == actual


@given(reserved=st.integers(0, 100), actual=st.integers(0, 100))
def test_settlement_replaces_only_the_reservation(reserved: int, actual: int) -> None:
    run = run_at(RunState.GATHERING)
    reservation = value(BudgetUsage.create(0, reserved, Decimal("0.5")))
    updated = value(
        value(run.reserve(reservation)).settle(
            reservation,
            value(BudgetUsage.create(0, actual, Decimal("0.01"))),
        )
    )
    assert updated.usage.tokens == actual
    assert updated.usage.estimated_cost == Decimal("0.01")


def test_settlement_cannot_refund_unreserved_usage_or_modify_terminal_runs() -> None:
    usage = value(BudgetUsage.create(1, 1, Decimal("0.1")))
    refund = run_at(RunState.GATHERING).settle(usage, BudgetUsage.zero())
    assert error_code(refund) == InvestigationErrorCode.INVALID_USAGE
    reserve = run_at(RunState.CLOSED).reserve(usage)
    assert error_code(reserve) == InvestigationErrorCode.INVALID_TRANSITION
    settle = run_at(RunState.CLOSED).settle(usage, BudgetUsage.zero())
    assert error_code(settle) == InvestigationErrorCode.INVALID_TRANSITION


@pytest.mark.parametrize("state", list(RunState))
def test_reservations_and_settlements_only_apply_while_work_is_active(state: RunState) -> None:
    run = run_at(state)
    usage = value(BudgetUsage.create(0, 0, Decimal(0)))
    active = state in {RunState.GATHERING, RunState.ANALYZING}
    assert isinstance(run.reserve(usage), Ok) == active
    assert isinstance(run.settle(BudgetUsage.zero(), usage), Ok) == active


def test_an_accepted_reservation_is_recorded_as_usage() -> None:
    run = run_at(RunState.GATHERING)
    reserved = value(BudgetUsage.create(2, 30, Decimal("0.25")))
    updated = value(run.reserve(reserved))
    assert updated.state == RunState.GATHERING
    assert updated.usage == reserved
    assert updated.failure_reason is None


def test_a_reservation_reaching_the_limit_exactly_is_accepted() -> None:
    run = run_at(RunState.GATHERING)
    limit = value(BudgetUsage.create(5, 100, Decimal("1.00")))
    assert value(run.reserve(limit)).state == RunState.GATHERING


@pytest.mark.parametrize(
    ("increment", "dimension"),
    [
        (value(BudgetUsage.create(6, 0, Decimal(0))), "tool_calls"),
        (value(BudgetUsage.create(0, 101, Decimal(0))), "tokens"),
        (value(BudgetUsage.create(0, 0, Decimal("1.01"))), "estimated_cost"),
    ],
)
def test_a_refused_reservation_names_the_dimension_and_consumes_nothing(
    increment: BudgetUsage, dimension: str
) -> None:
    run = run_at(RunState.GATHERING)
    refused = value(run.reserve(increment))
    assert refused.state == RunState.FAILED
    assert refused.usage == BudgetUsage.zero()
    assert refused.failure_reason == f"Budget reservation exceeds: {dimension}."


def test_settlement_keeps_other_usage_and_reports_an_overrun_on_the_total() -> None:
    run = run_at(RunState.ANALYZING)
    earlier = value(run.consume(value(BudgetUsage.create(4, 0, Decimal(0)))))
    reserved = value(BudgetUsage.create(1, 10, Decimal("0.10")))
    held = value(earlier.reserve(reserved))
    settled = value(held.settle(reserved, value(BudgetUsage.create(2, 5, Decimal("0.05")))))
    assert settled.state == RunState.FAILED
    assert settled.failure_reason == "Budget exceeded: tool_calls."
    assert (settled.usage.tool_calls, settled.usage.tokens) == (6, 5)
