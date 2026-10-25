from dataclasses import replace
from decimal import Decimal

import pytest

from casefile.features.evaluation.application.benchmark import (
    BenchmarkCase,
    EvaluateSuite,
)
from casefile.features.evaluation.domain.benchmark import (
    Citation,
    ExpectedAnalysis,
    Observation,
    ObservedSignal,
    ReferenceSignal,
    Thresholds,
    aggregate,
    assess,
    violations,
)

SOURCE = Citation("list_transactions", "txn-001")
EXPECTED = ExpectedAnalysis(
    "One volume signal.",
    (ReferenceSignal("volume:txn-001", "Above the declared volume.", 0.8, (SOURCE,)),),
    "escalate",
)
ACTUAL = Observation(
    "AWAITING_REVIEW",
    (ObservedSignal("volume:txn-001", (SOURCE,)),),
    "escalate",
    (SOURCE,),
    Decimal("0.003"),
    128,
    12.5,
)
LIMITS = Thresholds(30, 1.0, 1.0, 1.0, 0.0, Decimal("0.05"), 30000.0)


def test_metrics_count_false_signals_missing_signals_and_invented_sources() -> None:
    actual = replace(
        ACTUAL,
        signals=(ObservedSignal("other", (Citation("list_transactions", "invented"),)),),
        recommendation="close",
    )
    score = assess("case-001", "volume", EXPECTED, actual)
    assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 1, 1)
    assert score.precision == score.recall == 0
    assert not score.recommendation_correct
    assert score.invalid_sources == score.citations == 1


def test_micro_average_uses_counts_and_includes_failed_cases() -> None:
    good = assess("case-001", "volume", EXPECTED, ACTUAL)
    failed = assess("case-002", "volume", EXPECTED, replace(ACTUAL, state="FAILED", signals=()))
    total = aggregate((good, failed))
    assert total.signal_precision == 1
    assert total.signal_recall == 0.5
    assert total.recommendation_accuracy == total.completion_rate == 0.5
    assert total.estimated_cost == Decimal("0.006")
    assert total.latency_p50_ms == total.latency_p95_ms == 12.5
    assert violations((good, failed), LIMITS)


def test_empty_signals_are_perfect_only_when_nothing_was_expected() -> None:
    expected = replace(EXPECTED, signals=(), recommendation="close")
    actual = replace(ACTUAL, signals=(), recommendation="close")
    score = assess("case-001", "clear", expected, actual)
    assert score.precision == score.recall == 1
    total = aggregate((score,))
    assert total.invalid_source_rate == 0
    assert not violations((score,), replace(LIMITS, min_cases=1))
    missed = assess("case-002", "volume", EXPECTED, replace(ACTUAL, signals=()))
    assert missed.precision == missed.recall == 0


@pytest.mark.parametrize(
    "actual",
    [
        replace(ACTUAL, state="FAILED", signals=(), recommendation=None),
        replace(ACTUAL, state="ANALYZING", signals=(), recommendation=None, error="ValueError"),
        replace(ACTUAL, error="ProviderFailure"),
    ],
)
def test_a_run_that_did_not_complete_is_not_scored_per_case(actual: Observation) -> None:
    clear = replace(EXPECTED, signals=(), recommendation="close")
    for expected in (EXPECTED, clear):
        score = assess("case-001", "volume", expected, actual)
        assert not score.completed
        assert score.precision is None and score.recall is None


def test_incomplete_cases_keep_their_counts_in_the_micro_average() -> None:
    good = assess("case-001", "volume", EXPECTED, ACTUAL)
    failed = assess("case-002", "volume", EXPECTED, replace(ACTUAL, state="FAILED", signals=()))
    assert failed.precision is None and failed.recall is None
    assert (failed.true_positives, failed.false_negatives) == (0, 1)
    total = aggregate((good, failed))
    assert (total.signal_precision, total.signal_recall) == (1, 0.5)


def test_duplicate_predictions_are_false_positives_and_cannot_inflate_recall() -> None:
    actual = replace(ACTUAL, signals=ACTUAL.signals * 2)
    score = assess("case-001", "volume", EXPECTED, actual)
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 0)


@pytest.mark.parametrize(
    "actual",
    [
        replace(ACTUAL, state="FAILED"),
        replace(ACTUAL, error="ProviderFailure"),
        replace(ACTUAL, estimated_cost=Decimal("0.051")),
        replace(ACTUAL, latency_ms=30001),
        replace(ACTUAL, observed_sources=()),
        replace(ACTUAL, recommendation="close"),
        replace(ACTUAL, signals=()),
    ],
)
def test_each_regression_fails_the_gate(actual: Observation) -> None:
    score = assess("case-001", "volume", EXPECTED, actual)
    assert violations((score,), replace(LIMITS, min_cases=1))


def test_empty_suite_never_passes() -> None:
    with pytest.raises(ValueError, match="empty"):
        aggregate(())


class MemoryRunner:
    async def run(self, case: BenchmarkCase) -> Observation:
        return ACTUAL


@pytest.mark.asyncio
async def test_suite_keeps_case_and_dataset_identity() -> None:
    case = BenchmarkCase("case-001", "volume", "A fictional volume alert.", EXPECTED, "{}", "{}")
    report = await EvaluateSuite(MemoryRunner()).execute(
        (case,), replace(LIMITS, min_cases=1), mode="replay", dataset_sha256="a" * 64
    )
    assert report.passed
    assert report.dataset_sha256 == "a" * 64
    assert report.cases[0].case_id == "case-001"
    assert report.cases[0].expected_signals == ("volume:txn-001",)
