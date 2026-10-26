from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.budget import BudgetLimits, BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import HumanDecision, Run, RunState
from casefile.features.investigation.tests.factories import (
    AT,
    CASE_ID,
    CONCLUSION_ID,
    REVIEW_ID,
    REVIEWER_ID,
    RUN_ID,
    analysis,
    conclusion,
    decision,
    run_at,
    source,
    value,
)
from casefile.shared.ids import ConclusionId, RunId
from casefile.shared.result import Err, Ok, Result


@pytest.mark.parametrize("state", list(RunState))
@pytest.mark.parametrize("action", ["gather", "analyze", "submit", "close", "fail"])
def test_transition_matrix(state: RunState, action: str) -> None:
    run = run_at(state)
    expected = {
        "gather": (RunState.PENDING, RunState.GATHERING),
        "analyze": (RunState.GATHERING, RunState.ANALYZING),
        "submit": (RunState.ANALYZING, RunState.AWAITING_REVIEW),
        "close": (RunState.AWAITING_REVIEW, RunState.CLOSED),
    }
    result: Result[Run, InvestigationError]
    match action:
        case "gather":
            result = run.start_gathering()
        case "analyze":
            result = run.start_analysis()
        case "submit":
            result = run.submit_analysis(analysis())
        case "close":
            result = run.close(decision())
        case _:
            result = run.fail("Storage failure")
    if action == "fail" and state == RunState.CLOSED:
        assert isinstance(result, Err)
        assert result.error.code == InvestigationErrorCode.INVALID_TRANSITION
    elif action == "fail":
        assert value(result).state == RunState.FAILED
        assert value(result).failure_reason
        assert value(result).human_decision == run.human_decision
    elif state == expected[action][0]:
        assert value(result).state == expected[action][1]
    else:
        assert isinstance(result, Err)
        assert result.error.code == InvestigationErrorCode.INVALID_TRANSITION
    assert run.state == state


def test_close_requires_a_human_decision_for_the_same_run_and_all_conclusions() -> None:
    run = run_at(RunState.AWAITING_REVIEW)
    assert isinstance(run.close(None), Err)
    other_run = value(
        HumanDecision.create(
            RunId(UUID(int=99)),
            REVIEW_ID,
            REVIEWER_ID,
            (CONCLUSION_ID,),
            Recommendation.CLOSE,
            AT,
            confirmed=True,
        )
    )
    missing = value(
        HumanDecision.create(
            RUN_ID, REVIEW_ID, REVIEWER_ID, (), Recommendation.CLOSE, AT, confirmed=True
        )
    )
    foreign = value(
        HumanDecision.create(
            RUN_ID,
            REVIEW_ID,
            REVIEWER_ID,
            (ConclusionId(UUID(int=99)),),
            Recommendation.CLOSE,
            AT,
            confirmed=True,
        )
    )
    for receipt in (other_run, missing, foreign):
        assert isinstance(run.close(receipt), Err)
    assert value(run.close(decision())).human_decision == decision()
    assert run.state == RunState.AWAITING_REVIEW


def test_close_without_an_analysis_is_an_explicit_error_not_an_assertion() -> None:
    # A snapshot that reached AWAITING_REVIEW without an analysis can only come from corrupt
    # storage; it must fail closed through Result even when assertions are stripped (python -O).
    corrupt = run_at(RunState.AWAITING_REVIEW)._evolve(analysis=None)
    result = corrupt.close(decision())
    assert isinstance(result, Err)
    assert result.error.code == InvestigationErrorCode.INVALID_TRANSITION
    assert corrupt.state == RunState.AWAITING_REVIEW


def test_closed_run_is_terminal_and_cannot_fail() -> None:
    closed = run_at(RunState.CLOSED)
    result = closed.fail("Late storage failure")
    assert isinstance(result, Err)
    assert result.error.code == InvestigationErrorCode.INVALID_TRANSITION
    assert closed.state == RunState.CLOSED and closed.failure_reason is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_decision_needs_confirmation_and_aware_timestamp(confirmed: bool) -> None:
    result = HumanDecision.create(
        RUN_ID,
        REVIEW_ID,
        REVIEWER_ID,
        (CONCLUSION_ID,),
        Recommendation.CLOSE,
        AT,
        confirmed=confirmed,
    )
    assert isinstance(result, Ok) is confirmed
    assert isinstance(
        HumanDecision.create(
            RUN_ID,
            REVIEW_ID,
            REVIEWER_ID,
            (),
            Recommendation.CLOSE,
            datetime(2026, 1, 1),
            confirmed=True,
        ),
        Err,
    )
    assert isinstance(
        HumanDecision.create(
            RUN_ID,
            REVIEW_ID,
            REVIEWER_ID,
            (CONCLUSION_ID, CONCLUSION_ID),
            Recommendation.CLOSE,
            AT,
            confirmed=True,
        ),
        Err,
    )


def test_analysis_rejects_sources_not_observed_during_this_run() -> None:
    run = run_at(RunState.ANALYZING)
    invented = value(SourceReference.create("transactions", "invented"))
    wrong_tool = value(SourceReference.create("kyc", source().record_id))
    for reference in (invented, wrong_tool):
        item = value(
            Conclusion.create(CONCLUSION_ID, "Unverified", conclusion().confidence, (reference,))
        )
        report = value(Analysis.create("Summary", (item,), Recommendation.ESCALATE))
        result = run.submit_analysis(report)
        assert isinstance(result, Err)
        assert result.error.code == InvestigationErrorCode.UNKNOWN_SOURCE
    assert run.state == RunState.ANALYZING


@pytest.mark.parametrize("state", list(RunState))
def test_sources_are_registered_only_during_gathering(state: RunState) -> None:
    run = run_at(state)
    result = run.record_sources((source(), source()))
    if state == RunState.GATHERING:
        assert value(result).sources == (source(),)
    else:
        assert isinstance(result, Err)


@pytest.mark.parametrize(
    "cost", [Decimal("-1"), Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity")]
)
def test_invalid_costs_are_business_errors(cost: Decimal) -> None:
    assert isinstance(BudgetLimits.create(1, 1, cost), Err)
    assert isinstance(BudgetUsage.create(1, 1, cost), Err)


@pytest.mark.parametrize(("calls", "tokens"), [(-1, 1), (1, -1), (True, 1), (1, True)])
def test_negative_or_boolean_counters_are_rejected(calls: int, tokens: int) -> None:
    assert isinstance(BudgetLimits.create(calls, tokens, Decimal(0)), Err)
    assert isinstance(BudgetUsage.create(calls, tokens, Decimal(0)), Err)


@given(calls=st.integers(0, 20), tokens=st.integers(0, 200), cents=st.integers(0, 200))
def test_budget_caps_fail_the_run_in_any_exceeded_dimension(
    calls: int, tokens: int, cents: int
) -> None:
    run = run_at(RunState.GATHERING)
    usage = value(BudgetUsage.create(calls, tokens, Decimal(cents) / 100))
    charged = value(run.consume(usage))
    exceeded = calls > 5 or tokens > 100 or cents > 100
    assert (charged.state == RunState.FAILED) is exceeded
    assert charged.usage == usage
    assert bool(charged.failure_reason) is exceeded
    assert run.usage == BudgetUsage.zero()


def test_usage_is_cumulative_and_limits_are_inclusive() -> None:
    run = run_at(RunState.GATHERING)
    first = value(run.consume(value(BudgetUsage.create(2, 40, Decimal("0.40")))))
    exact = value(first.consume(value(BudgetUsage.create(3, 60, Decimal("0.60")))))
    assert exact.state == RunState.GATHERING
    exceeded = value(exact.consume(value(BudgetUsage.create(1, 0, Decimal(0)))))
    assert exceeded.state == RunState.FAILED
    assert exceeded.usage.tool_calls == 6
    assert exceeded.failure_reason is not None and "tool_calls" in exceeded.failure_reason
    assert isinstance(exceeded.consume(BudgetUsage.zero()), Err)


def test_tiny_cost_overrun_is_not_rounded_away() -> None:
    run = run_at(RunState.GATHERING)
    exact = value(run.consume(value(BudgetUsage.create(0, 0, Decimal("1")))))
    exceeded = value(exact.consume(value(BudgetUsage.create(0, 0, Decimal("1e-40")))))
    assert exceeded.state == RunState.FAILED
    assert exceeded.usage.estimated_cost > exact.limits.estimated_cost
    assert exceeded.failure_reason is not None and "estimated_cost" in exceeded.failure_reason


@pytest.mark.parametrize("state", list(RunState))
def test_usage_can_only_change_while_work_is_active(state: RunState) -> None:
    result = run_at(state).consume(BudgetUsage.zero())
    assert isinstance(result, Ok) == (state in {RunState.GATHERING, RunState.ANALYZING})


def test_zero_budget_stops_first_charge_and_failure_keeps_first_cause() -> None:
    run = Run.new(RUN_ID, CASE_ID, value(BudgetLimits.create(0, 0, Decimal(0))))
    assert isinstance(run.fail(" \n"), Err)
    active = value(run.start_gathering())
    failed = value(active.consume(value(BudgetUsage.create(0, 1, Decimal(0)))))
    assert failed.state == RunState.FAILED
    assert value(failed.fail("Later failure")) == failed


@given(st.lists(st.sampled_from(["gather", "analyze", "submit", "close", "fail"]), max_size=40))
def test_arbitrary_operations_never_close_without_human_review(actions: list[str]) -> None:
    run = run_at(RunState.PENDING)
    for action in actions:
        match action:
            case "gather":
                result = run.start_gathering()
            case "analyze":
                result = run.start_analysis()
            case "submit":
                result = run.submit_analysis(
                    value(Analysis.create("No signals", (), Recommendation.CLOSE))
                )
            case "close":
                result = run.close(None)
            case _:
                result = run.fail("Unavailable")
        if isinstance(result, Ok):
            run = result.value
        assert run.state != RunState.CLOSED
        assert run.human_decision is None


@given(
    st.sampled_from(list(RunState)),
    st.lists(
        st.sampled_from(["gather", "sources", "analyze", "submit", "close", "fail"]), max_size=40
    ),
)
def test_terminal_states_absorb_every_later_operation(start: RunState, actions: list[str]) -> None:
    run = run_at(start)
    for action in actions:
        before = run
        match action:
            case "gather":
                result = run.start_gathering()
            case "analyze":
                result = run.start_analysis()
            case "sources":
                result = run.record_sources((source(),))
            case "submit":
                result = run.submit_analysis(analysis())
            case "close":
                result = run.close(decision())
            case _:
                result = run.fail("Unavailable")
        if isinstance(result, Ok):
            run = result.value
        if before.state == RunState.CLOSED:
            assert run == before
        if before.state == RunState.FAILED:
            assert run.state == RunState.FAILED
            assert run.failure_reason == before.failure_reason
