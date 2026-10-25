from dataclasses import replace

import pytest

from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.agent_policy import candidates
from casefile.features.investigation.application.agent_types import EvidenceBatch
from casefile.features.investigation.application.banking_policy import banking_findings, is_concern
from casefile.features.investigation.application.tools import CaseDocument, ToolName


def evidence(number: int) -> tuple[EvidenceBatch, ...]:
    seed = load_seed(banking=True)
    customer = f"demo-customer-{number}"
    profile = next(record.to_record() for record in seed.kyc if record.customer_id == customer)
    return (
        EvidenceBatch(ToolName.KYC, customer, (profile,)),
        EvidenceBatch(
            ToolName.DOCUMENTS,
            customer,
            tuple(
                record.to_record() for record in seed.documents if record.customer_id == customer
            ),
        ),
        EvidenceBatch(
            ToolName.SANCTIONS,
            profile.legal_name,
            tuple(
                record.to_record() for record in seed.sanctions if record.name == profile.legal_name
            ),
        ),
    )


def change(
    batches: tuple[EvidenceBatch, ...],
    kind: str,
    values: dict[str, str],
    *,
    verification: str | None = None,
) -> tuple[EvidenceBatch, ...]:
    return tuple(
        replace(
            batch,
            records=tuple(
                replace(
                    record,
                    fields=tuple(
                        replace(field, value=values.get(field.name, field.value))
                        for field in record.fields
                    ),
                    verification=verification or record.verification,
                )
                if isinstance(record, CaseDocument) and record.kind == kind
                else record
                for record in batch.records
            ),
        )
        for batch in batches
    )


@pytest.mark.parametrize(
    "number,kind",
    [(101, "payment_fraud"), (102, "credit_document_fraud"), (103, "sanctions_identity_review")],
)
def test_missing_documents_produce_information_gaps(number: int, kind: str) -> None:
    batches = tuple(
        replace(batch, records=()) if batch.tool == ToolName.DOCUMENTS else batch
        for batch in evidence(number)
    )
    signals = banking_findings(batches, kind)
    assert signals and all(signal.key.startswith("gap:") for signal in signals)
    assert not any(is_concern(signal) for signal in signals)
    assert banking_findings((), kind) == ()


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("invoice", {"invoice_ref": "unrelated"}),
        ("invoice", {"amount": "NaN"}),
        ("invoice", {"currency": "USD"}),
        ("supplier_confirmation", {"change_authorized": "unknown"}),
    ],
)
def test_unreliable_payment_comparisons_request_verification(
    kind: str, fields: dict[str, str]
) -> None:
    assert (
        banking_findings(change(evidence(101), kind, fields), "payment_fraud")[0].key
        == "gap:payment_verification"
    )


def test_an_independently_authorized_account_change_is_mitigating() -> None:
    batches = change(
        evidence(101),
        "supplier_confirmation",
        {"change_authorized": "yes", "beneficiary_account": "DEMO-ACCOUNT-ALDER-NEW"},
    )
    assert not any(is_concern(s) for s in banking_findings(batches, "payment_fraud"))
    unchanged = change(
        evidence(101), "payment_instruction", {"beneficiary_account": "DEMO-ACCOUNT-ALDER-OLD"}
    )
    assert not any(is_concern(s) for s in banking_findings(unchanged, "payment_fraud"))


def test_amount_disagreement_is_not_hidden_by_the_account_check() -> None:
    batches = change(evidence(101), "purchase_order", {"amount": "18000.00"})
    assert any(
        s.key == "finding:payment_amount" for s in banking_findings(batches, "payment_fraud")
    )


@pytest.mark.parametrize(
    "kind,fields,verification",
    [
        ("credit_application", {"declared_monthly_income": "NaN"}, None),
        ("employment_confirmation", {}, "unverified"),
        ("bank_statement", {"employer_ref": "different"}, None),
        ("bank_statement", {"currency": "USD"}, None),
        ("bank_statement", {"month": "2026-12"}, None),
        ("payslip", {"month": "invalid"}, None),
        ("payslip", {"net_pay": "bad"}, None),
    ],
)
def test_income_comparisons_reject_unverified_unmatched_or_invalid_data(
    kind: str, fields: dict[str, str], verification: str | None
) -> None:
    signals = banking_findings(
        change(evidence(102), kind, fields, verification=verification), "credit_document_fraud"
    )
    assert any(s.key.startswith("gap:") for s in signals)


def test_matching_income_records_do_not_create_fraud_findings() -> None:
    batches = change(evidence(102), "credit_application", {"declared_monthly_income": "2600.00"})
    batches = change(batches, "payslip", {"net_pay": "2600.00"})
    signals = banking_findings(batches, "credit_document_fraud")
    assert not any(is_concern(signal) for signal in signals)
    assert any(signal.key == "context:employer_confirmation" for signal in signals)


@pytest.mark.parametrize(
    "kind,fields,verification",
    [
        ("watchlist_identity", {"date_of_birth": "invalid"}, None),
        ("registry_confirmation", {"document_ref": "different"}, None),
        ("identity_record", {}, "unverified"),
        ("watchlist_identity", {"sanction_record_id": "sanction-999"}, None),
        ("watchlist_identity", {"country": "invalid"}, None),
    ],
)
def test_inconsistent_identity_data_never_clears_a_name_hit(
    kind: str, fields: dict[str, str], verification: str | None
) -> None:
    signals = banking_findings(
        change(evidence(103), kind, fields, verification=verification), "sanctions_identity_review"
    )
    assert signals[0].key == "gap:identity_verification"


def test_partial_and_full_biographical_matches_are_distinguished() -> None:
    partial = change(evidence(103), "watchlist_identity", {"date_of_birth": "1988-04-12"})
    assert any(
        s.key == "gap:partial_identity"
        for s in banking_findings(partial, "sanctions_identity_review")
    )
    full = change(partial, "watchlist_identity", {"country": "FR"})
    assert any(
        s.key == "finding:identity_match"
        for s in banking_findings(full, "sanctions_identity_review")
    )


def test_injection_in_a_document_is_retained_as_a_sourced_concern() -> None:
    signals = candidates(evidence(101), frozenset({"doc-103"}), kind="payment_fraud")
    injected = [s for s in signals if s.key == "instruction:doc-103"]
    assert len(injected) == 1
    assert injected[0].sources[0].tool == ToolName.DOCUMENTS
