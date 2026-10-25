"""Task-level calibration uses outcome probabilities, not provider certainty statistics."""

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
