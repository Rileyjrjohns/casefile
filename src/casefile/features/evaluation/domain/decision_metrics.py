"""Task-level calibration uses outcome probabilities, not provider certainty statistics."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import ceil


@dataclass(frozen=True, slots=True)
class DecisionMeasurement:
    case_id: str
    purpose: str
    correct: bool
    selected_probability: float
    latency_ms: float
    cost: Decimal
    error: str | None = None


def summarize(rows: tuple[DecisionMeasurement, ...]) -> dict[str, str | int | float]:
    if not rows:
        raise ValueError("Cannot summarize an empty decision benchmark.")
    ece = 0.0
    for index in range(10):
        bucket = [r for r in rows if min(9, int(r.selected_probability * 10)) == index]
        if bucket:
            ece += abs(
                sum(r.correct for r in bucket) - sum(r.selected_probability for r in bucket)
            ) / len(rows)
    latencies = sorted(r.latency_ms for r in rows)
    return {
        "count": len(rows),
        "accuracy": sum(r.correct for r in rows) / len(rows),
        "ece": ece,
        "latency_p50_ms": latencies[ceil(len(rows) * 0.5) - 1],
        "latency_p95_ms": latencies[ceil(len(rows) * 0.95) - 1],
        "cost_per_case": str(
            sum((r.cost for r in rows), Decimal(0)) / len({r.case_id for r in rows})
        ),
        "errors": sum(r.error is not None for r in rows),
    }


@dataclass(frozen=True, slots=True)
class DecisionThresholds:
    """Regression floors on one offline adapter's accuracy per decision purpose.

    A floor records what the adapter achieved when it was set. It guards against regressions and
    is not a quality target: the demo policy is allowed to score low on a purpose.
    """

    adapter: str
    min_accuracy: tuple[tuple[str, float], ...]


def accuracy_violations(
    accuracy: Mapping[str, float], thresholds: DecisionThresholds
) -> tuple[str, ...]:
    """Name every gated purpose whose measured accuracy is missing or below its floor."""
    violations: list[str] = []
    for purpose, floor in thresholds.min_accuracy:
        measured = accuracy.get(purpose)
        if measured is None:
            violations.append(f"{thresholds.adapter}/{purpose}: not measured.")
        elif measured < floor:
            violations.append(
                f"{thresholds.adapter}/{purpose}: accuracy {measured:.3f} "
                f"is below the minimum {floor:.3f}."
            )
    return tuple(violations)
