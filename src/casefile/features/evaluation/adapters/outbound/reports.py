"""Export machine-readable metrics and a compact, reviewable Markdown report."""

from pathlib import Path

from pydantic import TypeAdapter

from casefile.features.evaluation.application.benchmark import SuiteReport


def markdown(report: SuiteReport) -> str:
    metrics = report.metrics
    cost_note = (
        "Replay costs and token counts come from synthetic fixtures; they are not provider bills. "
        "Measured latency is local replay execution, not live model latency."
        if report.mode == "replay"
        else "Costs are estimates from configured token rates and reported usage. "
        "Latency includes the live narrative call and local orchestration. "
        "Failed calls can retain reservations when actual billing is unknown."
    )
    lines = [
        "# Casefile evaluation report",
        "",
        f"Result: **{'PASS' if report.passed else 'FAIL'}** · Mode: `{report.mode}`",
        "",
        f"Dataset SHA-256: `{report.dataset_sha256}`",
        "",
        cost_note,
        "",
        "This measures demonstration rules and structural narrative validity. "
        "It does not measure fraud detection accuracy, prose truthfulness "
        "or DecisionModel adapters.",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Cases | {metrics.cases} |",
        f"| Signal precision (micro) | {metrics.signal_precision:.4f} |",
        f"| Signal recall (micro) | {metrics.signal_recall:.4f} |",
        f"| Recommendation accuracy | {metrics.recommendation_accuracy:.4f} |",
        f"| Invalid accepted sources | {metrics.invalid_sources}/{metrics.citations} |",
        f"| Completion rate | {metrics.completion_rate:.4f} |",
        f"| Total estimated cost (USD) | {metrics.estimated_cost} |",
        f"| Mean estimated cost per case (USD) | {metrics.mean_cost_per_case} |",
        f"| Latency p50 / p95 (ms) | {metrics.latency_p50_ms:.3f} / {metrics.latency_p95_ms:.3f} |",
        "",
        "## Per case",
        "",
        "| Case | Category | State | Precision | Recall | Recommendation | Invalid sources | "
        "Cost (USD) | Tokens | Latency (ms) | Error |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    lines.extend(
        f"| {score.case_id} | {score.category} | {score.state} | {score.precision:.3f} | "
        f"{score.recall:.3f} | {'correct' if score.recommendation_correct else 'incorrect'} | "
        f"{score.invalid_sources}/{score.citations} | {score.estimated_cost} | {score.tokens} | "
        f"{score.latency_ms:.3f} | {score.error or '—'} |"
        for score in report.cases
    )
    lines.extend(["", "## Gate violations", ""])
    lines.extend(f"- {failure}" for failure in report.violations)
    if not report.violations:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def write_report(report: SuiteReport, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_bytes(TypeAdapter(SuiteReport).dump_json(report, indent=2))
    (directory / "report.md").write_text(markdown(report), encoding="utf-8")
