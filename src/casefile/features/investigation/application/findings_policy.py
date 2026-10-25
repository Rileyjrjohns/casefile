"""Versioned additions to the historical fictional evidence policy."""

from datetime import UTC
from decimal import Decimal

from casefile.features.investigation.application.agent_types import (
    EvidenceBatch,
    Signal,
    SourceLink,
)
from casefile.features.investigation.application.tools import (
    AlertRecord,
    KycProfile,
    SanctionEntry,
    ToolName,
    Transaction,
)

LEGACY_FINDINGS = "findings-v1"
CURRENT_FINDINGS = "findings-v2"
FINDINGS_VERSIONS = frozenset({LEGACY_FINDINGS, CURRENT_FINDINGS})


def additional_findings(evidence: tuple[EvidenceBatch, ...]) -> tuple[Signal, ...]:
    records = tuple(r for batch in evidence for r in batch.records)
    profile = next((r for r in records if isinstance(r, KycProfile)), None)
    signals: list[Signal] = []
    transactions = {r.record_id: r for r in records if isinstance(r, Transaction)}
    if profile is not None:
        months: dict[str, list[Transaction]] = {}
        for record in transactions.values():
            if record.currency != "EUR":
                signals.append(
                    Signal(
                        f"gap:currency:{record.record_id}",
                        f"Transfer {record.record_id} uses {record.currency}; the fictional KYC "
                        "baseline is in EUR. Obtain a comparable amount before evaluating volume.",
                        1.0,
                        (
                            SourceLink(ToolName.TRANSACTIONS, record.record_id),
                            SourceLink(ToolName.KYC, profile.record_id),
                        ),
                    )
                )
                continue
            month = record.booked_at.astimezone(UTC).strftime("%Y-%m")
            months.setdefault(month, []).append(record)
        for month, transfers in sorted(months.items()):
            total = sum((r.amount for r in transfers), Decimal(0))
            if (
                len(transfers) < 2
                or total <= profile.expected_monthly_volume
                or any(r.amount > profile.expected_monthly_volume for r in transfers)
            ):
                continue
            signals.append(
                Signal(
                    f"monthly_volume:{month}:EUR",
                    f"Observed EUR transfers in {month} total {total:.2f}, exceeding the declared "
                    f"monthly volume of EUR {profile.expected_monthly_volume:.2f}. "
                    "This compares the returned snapshot, "
                    "not a verified complete monthly statement.",
                    1.0,
                    (
                        SourceLink(ToolName.KYC, profile.record_id),
                        *(
                            SourceLink(ToolName.TRANSACTIONS, r.record_id)
                            for r in sorted(transfers, key=lambda r: r.record_id)
                        ),
                    ),
                )
            )
    if not any(isinstance(r, SanctionEntry) for r in records):
        for alert in records:
            if (
                isinstance(alert, AlertRecord)
                and alert.kind == "sanctions_match"
                and alert.status == "open"
            ):
                signals.append(
                    Signal(
                        f"gap:unresolved_sanctions:{alert.record_id}",
                        "An open sanctions alert has no returned list match. "
                        "An empty tool response does not resolve the alert; "
                        "obtain identification evidence for human review.",
                        1.0,
                        (SourceLink(ToolName.ALERTS, alert.record_id),),
                    )
                )
    return tuple(signals)
