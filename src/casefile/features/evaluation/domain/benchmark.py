"""Structural benchmark metrics, independent of any investigation implementation."""

from dataclasses import dataclass
from decimal import Decimal
from math import ceil


@dataclass(frozen=True, slots=True)
class Citation:
    tool: str
    record_id: str


@dataclass(frozen=True, slots=True)
class ReferenceSignal:
    key: str
    statement: str
    confidence: float
    sources: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class ExpectedAnalysis:
    summary: str
    signals: tuple[ReferenceSignal, ...]
    recommendation: str


@dataclass(frozen=True, slots=True)
class ObservedSignal:
    key: str
    sources: tuple[Citation, ...]
    statement: str = ""
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    state: str
    signals: tuple[ObservedSignal, ...]
    recommendation: str | None
    observed_sources: tuple[Citation, ...]
    estimated_cost: Decimal
    tokens: int
    latency_ms: float
    error: str | None = None
    model: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class Thresholds:
    min_cases: int
    min_signal_precision: float
    min_signal_recall: float
    min_recommendation_accuracy: float
    max_invalid_source_rate: float
    max_cost_per_case: Decimal
    max_latency_ms: float


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    category: str
    expected_signals: tuple[str, ...]
    predicted_signals: tuple[str, ...]
    expected_recommendation: str
    predicted_recommendation: str | None
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    recommendation_correct: bool
    invalid_sources: int
    citations: int
    completed: bool
    state: str
    estimated_cost: Decimal
    tokens: int
    latency_ms: float
    error: str | None
    model: str | None
    summary: str | None
    conclusions: tuple[ObservedSignal, ...]


@dataclass(frozen=True, slots=True)
class Metrics:
    cases: int
    true_positives: int
    false_positives: int
    false_negatives: int
    signal_precision: float
    signal_recall: float
    recommendation_accuracy: float
    invalid_source_rate: float
    invalid_sources: int
    citations: int
    completion_rate: float
    estimated_cost: Decimal
    mean_cost_per_case: Decimal
    max_cost_per_case: Decimal
    tokens: int
    latency_p50_ms: float
    latency_p95_ms: float
    max_latency_ms: float


def rates(tp: int, fp: int, fn: int) -> tuple[float, float]:
    # Empty predictions are perfect only when the reference is empty too.
    precision = tp / (tp + fp) if tp + fp else float(fn == 0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    return precision, recall


def assess(
    case_id: str, category: str, expected: ExpectedAnalysis, actual: Observation
) -> CaseScore:
    expected_keys = {signal.key for signal in expected.signals}
    predicted = tuple(signal.key for signal in actual.signals)
    tp = len(expected_keys & set(predicted))
    fp, fn = len(predicted) - tp, len(expected_keys - set(predicted))
    precision, recall = rates(tp, fp, fn)
    sources = tuple(source for signal in actual.signals for source in signal.sources)
    completed = actual.state == "AWAITING_REVIEW" and actual.error is None
    return CaseScore(
        case_id,
        category,
        tuple(sorted(expected_keys)),
        predicted,
        expected.recommendation,
        actual.recommendation,
        tp,
        fp,
        fn,
        precision,
        recall,
        completed and actual.recommendation == expected.recommendation,
        sum(source not in actual.observed_sources for source in sources),
        len(sources),
        completed,
        actual.state,
        actual.estimated_cost,
        actual.tokens,
        actual.latency_ms,
        actual.error,
        actual.model,
        actual.summary,
        actual.signals,
    )


def aggregate(scores: tuple[CaseScore, ...]) -> Metrics:
    if not scores:
        raise ValueError("An empty evaluation suite has no meaningful metrics.")
    tp = sum(score.true_positives for score in scores)
    fp = sum(score.false_positives for score in scores)
    fn = sum(score.false_negatives for score in scores)
    precision, recall = rates(tp, fp, fn)
    invalid = sum(score.invalid_sources for score in scores)
    citations = sum(score.citations for score in scores)
    cost = sum((score.estimated_cost for score in scores), Decimal(0))
    latencies = sorted(score.latency_ms for score in scores)
    count = len(scores)
    return Metrics(
        count,
        tp,
        fp,
        fn,
        precision,
        recall,
        sum(score.recommendation_correct for score in scores) / count,
        invalid / citations if citations else 0.0,
        invalid,
        citations,
        sum(score.completed for score in scores) / count,
        cost,
        cost / count,
        max(score.estimated_cost for score in scores),
        sum(score.tokens for score in scores),
        latencies[ceil(count * 0.5) - 1],
        latencies[ceil(count * 0.95) - 1],
        latencies[-1],
    )


def violations(scores: tuple[CaseScore, ...], limits: Thresholds) -> tuple[str, ...]:
    metrics = aggregate(scores)
    checks = (
        (metrics.cases >= limits.min_cases, "Case count is below the required minimum."),
        (metrics.completion_rate == 1, "Every case must reach AWAITING_REVIEW without errors."),
        (metrics.signal_precision >= limits.min_signal_precision, "Signal precision regressed."),
        (metrics.signal_recall >= limits.min_signal_recall, "Signal recall regressed."),
        (
            metrics.recommendation_accuracy >= limits.min_recommendation_accuracy,
            "Recommendation accuracy regressed.",
        ),
        (
            metrics.invalid_source_rate <= limits.max_invalid_source_rate,
            "Invalid source rate exceeded the limit.",
        ),
        (
            metrics.max_cost_per_case <= limits.max_cost_per_case,
            "Per-case cost exceeded the limit.",
        ),
        (metrics.max_latency_ms <= limits.max_latency_ms, "Per-case latency exceeded the limit."),
    )
    return tuple(message for passed, message in checks if not passed)
