"""Extract verifiable candidate facts and validate narrative provenance."""

from uuid import uuid5

from casefile.features.investigation.application.agent_types import (
    EvidenceBatch,
    ModelResponse,
    Signal,
    SourceLink,
)
from casefile.features.investigation.application.banking_policy import (
    BANKING_KINDS,
    banking_findings,
)
from casefile.features.investigation.application.decisions import state_json
from casefile.features.investigation.application.findings_policy import (
    CURRENT_FINDINGS,
    FINDINGS_VERSIONS,
    additional_findings,
)
from casefile.features.investigation.application.narrative_policy import summary_contradictions
from casefile.features.investigation.application.tools import (
    AlertRecord,
    CaseDocument,
    InvestigatorNote,
    KycProfile,
    SanctionEntry,
    ToolName,
    Transaction,
)
from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.shared.ids import ConclusionId, RunId
from casefile.shared.result import Err, Result


def candidates(
    evidence: tuple[EvidenceBatch, ...],
    injections: frozenset[str],
    *,
    kind: str = "",
    policy_version: str = CURRENT_FINDINGS,
) -> tuple[Signal, ...]:
    if policy_version not in FINDINGS_VERSIONS:
        raise ValueError("Unknown findings policy version.")
    records = tuple(record for batch in evidence for record in batch.records)
    profile = next((item for item in records if isinstance(item, KycProfile)), None)
    signals: list[Signal] = []
    for record in records:
        if (
            isinstance(record, Transaction)
            and profile
            and (policy_version != CURRENT_FINDINGS or record.currency == "EUR")
            and record.amount > profile.expected_monthly_volume
        ):
            signals.append(
                Signal(
                    f"volume:{record.record_id}",
                    f"Transfer {record.record_id} exceeds the declared monthly volume.",
                    0.8,
                    (
                        SourceLink(ToolName.TRANSACTIONS, record.record_id),
                        SourceLink(ToolName.KYC, profile.record_id),
                    ),
                )
            )
        elif isinstance(record, SanctionEntry):
            signals.append(
                Signal(
                    f"sanctions:{record.record_id}",
                    "Exact name match on the fictional demonstration list.",
                    0.9,
                    (SourceLink(ToolName.SANCTIONS, record.record_id),),
                )
            )
        elif (
            isinstance(record, AlertRecord)
            and record.kind == "kyc_mismatch"
            and record.status == "open"
        ):
            signals.append(
                Signal(
                    f"kyc:{record.record_id}",
                    "An open alert reports inconsistent KYC information.",
                    0.7,
                    (SourceLink(ToolName.ALERTS, record.record_id),),
                )
            )
        elif (
            isinstance(record, (InvestigatorNote, CaseDocument)) and record.record_id in injections
        ):
            signals.append(
                Signal(
                    f"instruction:{record.record_id}",
                    "A document contains instruction-like untrusted text."
                    if isinstance(record, CaseDocument)
                    else "A note contains instruction-like untrusted text.",
                    0.6,
                    (
                        SourceLink(
                            ToolName.DOCUMENTS
                            if isinstance(record, CaseDocument)
                            else ToolName.NOTES,
                            record.record_id,
                        ),
                    ),
                )
            )
    if kind in BANKING_KINDS:
        return banking_findings(evidence, kind) + tuple(
            signal for signal in signals if signal.key.startswith("instruction:")
        )
    return tuple(signals) + (
        additional_findings(evidence) if policy_version == CURRENT_FINDINGS else ()
    )


def validate_analysis(
    run_id: RunId,
    signals: tuple[Signal, ...],
    recommendation: Recommendation,
    response: ModelResponse,
    observed: tuple[SourceReference, ...],
) -> Result[Analysis, InvestigationError]:
    def rejected(message: str) -> Err[InvestigationError]:
        return Err(InvestigationError(InvestigationErrorCode.UNKNOWN_SOURCE, message))

    # Conclusion text is the approved signal's own statement; the model's per-statement wording
    # stays in the journal but is never presented to the reviewer. The checks below keep the
    # model honest about structure: one statement per approved signal, with exactly its sources.
    expected = {signal.key: signal for signal in signals}
    keys = [item.key for item in response.statements]
    if len(set(keys)) != len(keys) or set(keys) != set(expected):
        return rejected("Model statements must cover exactly the approved signals.")
    known = {(source.tool, source.record_id) for source in observed}
    conclusions = []
    for statement in response.statements:
        signal = expected[statement.key]
        sources = {(source.tool, source.record_id) for source in statement.sources}
        if (
            not sources
            or len(sources) != len(statement.sources)
            or not sources <= known
            or sources != {(source.tool, source.record_id) for source in signal.sources}
        ):
            return rejected("Model cited an unobserved or unrelated source.")
        confidence = Confidence.create(signal.confidence)
        if isinstance(confidence, Err):
            return confidence
        exact = tuple(source for source in observed if (source.tool, source.record_id) in sources)
        conclusion = Conclusion.create(
            ConclusionId(uuid5(run_id, signal.key)),
            signal.statement,
            confidence.value,
            exact,
        )
        if isinstance(conclusion, Err):
            return conclusion
        conclusions.append(conclusion.value)
    contradictions = summary_contradictions(response.summary, recommendation, signals)
    if contradictions:
        return Err(
            InvestigationError(
                InvestigationErrorCode.INVALID_ANALYSIS,
                f"Model summary contradicts the analysis: {'; '.join(contradictions)}.",
            )
        )
    return Analysis.create(response.summary, tuple(conclusions), recommendation)


def risk_inputs(
    evidence: tuple[EvidenceBatch, ...], *, eur_only: bool = False
) -> tuple[tuple[str, str], ...]:
    records = tuple(record for batch in evidence for record in batch.records)
    profile = next((r for r in records if isinstance(r, KycProfile)), None)
    if profile is None or profile.expected_monthly_volume <= 0:
        return ()
    transactions = sorted(
        (
            r
            for r in records
            if isinstance(r, Transaction) and (not eur_only or r.currency == "EUR")
        ),
        key=lambda r: (r.booked_at, r.record_id),
    )
    return tuple(
        (
            transaction.record_id,
            state_json(
                {
                    "amount_ratio": min(
                        100_000.0, float(transaction.amount / profile.expected_monthly_volume)
                    ),
                    "new_counterparty": not any(
                        t.counterparty == transaction.counterparty
                        and t.booked_at < transaction.booked_at
                        for t in transactions
                    ),
                    "cross_border": transaction.country != profile.country,
                    "night": transaction.booked_at.hour < 6,
                    "transactions_24h": sum(
                        0 <= (transaction.booked_at - t.booked_at).total_seconds() < 86400
                        for t in transactions
                    ),
                    "kyc_age_days": min(
                        36500, max(0, (transaction.booked_at - profile.reviewed_at).days)
                    ),
                }
            ),
        )
        for transaction in transactions
    )
