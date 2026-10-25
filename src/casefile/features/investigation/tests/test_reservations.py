from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.run import RunState
from casefile.features.investigation.tests.factories import run_at, value
from casefile.shared.result import Err


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
    assert isinstance(run_at(RunState.GATHERING).settle(usage, BudgetUsage.zero()), Err)
    assert isinstance(run_at(RunState.CLOSED).reserve(usage), Err)
    assert isinstance(run_at(RunState.CLOSED).settle(usage, BudgetUsage.zero()), Err)
