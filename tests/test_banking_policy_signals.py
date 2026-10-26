"""Table-driven banking dossier checks: exact signal keys, cited sources and single mismatches.

Every row starts from the bundled fictional dossier of one workflow and applies edits to named
records, so a row names the one inconsistency it introduces and the complete signal list that
must result.
"""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from casefile.features.investigation.application.agent_types import EvidenceBatch, SourceLink
from casefile.features.investigation.application.banking_policy import banking_findings
from casefile.features.investigation.application.tools import (
    CaseDocument,
    EvidenceField,
    KycProfile,
    ToolName,
)
from tests.test_banking_policy import evidence

type Batches = tuple[EvidenceBatch, ...]
type Edit = Callable[[Batches], Batches]
type Expected = tuple[str, tuple[SourceLink, ...]]

PAYMENT, CREDIT, IDENTITY = "payment_fraud", "credit_document_fraud", "sanctions_identity_review"


def link(record_id: str) -> SourceLink:
    tool = (
        ToolName.SANCTIONS
        if record_id.startswith("sanction-")
        else ToolName.KYC
        if record_id.startswith("kyc-")
        else ToolName.DOCUMENTS
    )
    return SourceLink(tool, record_id)


def sig(key: str, *record_ids: str) -> Expected:
    return key, tuple(link(record_id) for record_id in record_ids)


def rewrite(
    batches: Batches, record_id: str, change: Callable[[CaseDocument], CaseDocument]
) -> Batches:
    return tuple(
        replace(
            batch,
            records=tuple(
                change(r) if isinstance(r, CaseDocument) and r.record_id == record_id else r
                for r in batch.records
            ),
        )
        for batch in batches
    )


def edit(record_id: str, **values: str) -> Edit:
    """Replace named fields of one document."""

    def change(document: CaseDocument) -> CaseDocument:
        known = {field.name for field in document.fields}
        assert set(values) <= known, f"{record_id} has no field {set(values) - known}"
        return replace(
            document,
            fields=tuple(
                EvidenceField(field.name, values.get(field.name, field.value))
                for field in document.fields
            ),
        )

    return lambda batches: rewrite(batches, record_id, change)


def unverified(record_id: str) -> Edit:
    return lambda batches: rewrite(
        batches, record_id, lambda document: replace(document, verification="unverified")
    )


def drop(record_id: str) -> Edit:
    def apply(batches: Batches) -> Batches:
        return tuple(
            replace(batch, records=tuple(r for r in batch.records if r.record_id != record_id))
            for batch in batches
        )

    return apply


def copy(record_id: str, new_id: str) -> Edit:
    """Add a second record identical to an existing one, which makes its kind ambiguous."""

    def apply(batches: Batches) -> Batches:
        return tuple(
            replace(
                batch,
                records=tuple(
                    item
                    for r in batch.records
                    for item in (
                        (r, replace(r, record_id=new_id)) if r.record_id == record_id else (r,)
                    )
                ),
            )
            for batch in batches
        )

    return apply


def drop_tool(tool: ToolName) -> Edit:
    def apply(batches: Batches) -> Batches:
        return tuple(batch for batch in batches if batch.tool != tool)

    return apply


def run(number: int, kind: str, edits: tuple[Edit, ...]) -> list[Expected]:
    batches = evidence(number)
    for apply in edits:
        batches = apply(batches)
    signals = banking_findings(batches, kind)
    assert all(signal.confidence == 1.0 for signal in signals)
    return [(signal.key, signal.sources) for signal in signals]


APPROVED = sig("context:approved_purchase", "doc-102", "doc-101", "doc-103")
AMOUNT = sig("finding:payment_amount", "doc-102", "doc-101", "doc-103")
MISMATCH = sig("finding:beneficiary_mismatch", "doc-103", "doc-102", "doc-104")
DENIAL = sig("finding:supplier_denial", "doc-104", "doc-103")
UNCHANGED = sig("context:unchanged_account", "doc-103", "doc-102")
CONFIRMED = sig("context:confirmed_account_change", "doc-103", "doc-104", "doc-102")
PAYMENT_GAP = sig("gap:payment_verification", "doc-102", "doc-101", "doc-103", "doc-104")
OLD, NEW = "DEMO-ACCOUNT-ALDER-OLD", "DEMO-ACCOUNT-ALDER-NEW"

PAYMENT_ROWS = [
    pytest.param((), [APPROVED, MISMATCH, DENIAL], id="baseline-diversion"),
    pytest.param(
        (edit("doc-103", beneficiary_account=OLD),), [APPROVED, UNCHANGED], id="unchanged-account"
    ),
    pytest.param(
        (edit("doc-104", change_authorized="yes", beneficiary_account=NEW),),
        [APPROVED, CONFIRMED],
        id="confirmed-change",
    ),
    pytest.param(
        (edit("doc-104", change_authorized="yes"),),
        [APPROVED, MISMATCH],
        id="authorized-but-other-account",
    ),
    pytest.param(
        (edit("doc-104", beneficiary_account=NEW),),
        [APPROVED, MISMATCH],
        id="same-account-without-authorization",
    ),
    pytest.param(
        (edit("doc-101", amount="18000.00"),), [AMOUNT, MISMATCH, DENIAL], id="order-amount"
    ),
    pytest.param(
        (edit("doc-103", amount="18450.01"),), [AMOUNT, MISMATCH, DENIAL], id="instruction-amount"
    ),
    pytest.param(
        (edit("doc-102", amount="18450.01"),), [AMOUNT, MISMATCH, DENIAL], id="invoice-amount"
    ),
    pytest.param(
        (
            edit("doc-101", amount="18000.00"),
            edit("doc-104", change_authorized="yes", beneficiary_account=NEW),
        ),
        [AMOUNT, CONFIRMED],
        id="amount-gap-survives-a-confirmed-change",
    ),
]


@pytest.mark.parametrize(("edits", "expected"), PAYMENT_ROWS)
def test_payment_signals_and_their_sources(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(101, PAYMENT, edits) == expected


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param(
            (edit("doc-103", invoice_ref="DEMO-INV-OTHER"),), id="instruction-invoice-ref"
        ),
        pytest.param(
            (edit("doc-104", invoice_ref="DEMO-INV-OTHER"),), id="confirmation-invoice-ref"
        ),
        pytest.param(
            (edit("doc-102", purchase_order_ref="DEMO-PO-OTHER"),), id="invoice-order-reference"
        ),
        pytest.param(
            (edit("doc-102", invoice_ref=""),), id="invoice-reference-removed-on-one-side"
        ),
        pytest.param(
            (
                edit("doc-102", invoice_ref=""),
                edit("doc-103", invoice_ref=""),
                edit("doc-104", invoice_ref=""),
            ),
            id="invoice-reference-missing-everywhere",
        ),
        pytest.param(
            (edit("doc-102", purchase_order_ref=""), edit("doc-101", purchase_order_ref="")),
            id="order-reference-missing-everywhere",
        ),
        pytest.param((edit("doc-101", currency="USD"),), id="order-currency"),
        pytest.param((edit("doc-103", currency="USD"),), id="instruction-currency"),
        pytest.param(
            (
                edit("doc-101", currency="USD"),
                edit("doc-102", currency="USD"),
                edit("doc-103", currency="USD"),
            ),
            id="consistent-but-not-euro",
        ),
        pytest.param((edit("doc-102", amount="NaN"),), id="invoice-amount-invalid"),
        pytest.param((edit("doc-101", amount="18450"),), id="order-amount-without-cents"),
        pytest.param((edit("doc-103", amount="018450.00"),), id="instruction-amount-leading-zero"),
        pytest.param((edit("doc-102", beneficiary_account=""),), id="invoice-without-account"),
        pytest.param((edit("doc-103", beneficiary_account=""),), id="instruction-without-account"),
        pytest.param((edit("doc-104", beneficiary_account=""),), id="confirmation-without-account"),
        pytest.param(
            (edit("doc-104", change_authorized="maybe"),), id="unrecognized-authorization"
        ),
        pytest.param((unverified("doc-102"),), id="invoice-unverified"),
        pytest.param((unverified("doc-101"),), id="order-unverified"),
        pytest.param((unverified("doc-104"),), id="confirmation-unverified"),
    ],
)
def test_one_payment_inconsistency_is_enough_to_withhold_the_comparison(
    edits: tuple[Edit, ...],
) -> None:
    assert run(101, PAYMENT, edits) == [PAYMENT_GAP]


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        pytest.param((drop("doc-101"),), [sig("gap:payment_documents", "kyc-101")], id="no-order"),
        pytest.param(
            (drop("doc-102"),), [sig("gap:payment_documents", "kyc-101")], id="no-invoice"
        ),
        pytest.param(
            (drop("doc-103"),), [sig("gap:payment_documents", "kyc-101")], id="no-instruction"
        ),
        pytest.param(
            (drop("doc-104"),), [sig("gap:payment_documents", "kyc-101")], id="no-confirmation"
        ),
        pytest.param(
            (copy("doc-102", "doc-902"),),
            [sig("gap:payment_documents", "kyc-101")],
            id="two-invoices-are-ambiguous",
        ),
        pytest.param(
            (drop_tool(ToolName.KYC), drop("doc-101")),
            [sig("gap:payment_documents", "doc-102")],
            id="source-falls-back-to-the-first-record-returned",
        ),
    ],
)
def test_incomplete_or_ambiguous_payment_packages_cite_the_first_record_returned(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(101, PAYMENT, edits) == expected


MATCHES = [
    sig(f"context:income_match:doc-{n}", f"doc-{n}", f"doc-{n + 3}") for n in (202, 203, 204)
]
BASE_GAPS = [
    sig(f"finding:income_gap:doc-{n}", f"doc-{n}", f"doc-{n + 3}") for n in (202, 203, 204)
]
DISCREPANCY = sig("finding:employer_discrepancy", "doc-201", "doc-208")
EMPLOYER_MATCH = sig("context:employer_confirmation", "doc-201", "doc-208")
EXPLANATION = sig("context:applicant_explanation", "doc-209", "doc-208")
INCOME_GAP = sig(
    "gap:income_verification",
    "doc-201",
    "doc-208",
    "doc-202",
    "doc-203",
    "doc-204",
    "doc-205",
    "doc-206",
    "doc-207",
)
PERIODS_WITH_COPIES = sig(
    "gap:income_periods",
    "doc-202",
    "doc-203",
    "doc-204",
    "doc-205",
    "doc-905",
    "doc-206",
    "doc-207",
)
PERIODS = sig(
    "gap:income_periods", "doc-202", "doc-203", "doc-204", "doc-205", "doc-206", "doc-207"
)
CREDITS_4200 = (
    edit("doc-205", salary_credit="4200.00"),
    edit("doc-206", salary_credit="4200.00"),
    edit("doc-207", salary_credit="4200.00"),
)

CREDIT_ROWS = [
    pytest.param((), [*BASE_GAPS, DISCREPANCY, EXPLANATION], id="baseline-inflated-income"),
    pytest.param(
        (*CREDITS_4200, edit("doc-208", monthly_net_pay="4200.00")),
        [*MATCHES, EMPLOYER_MATCH, EXPLANATION],
        id="everything-matches",
    ),
    pytest.param(
        (edit("doc-206", salary_credit="4200.00"),),
        [BASE_GAPS[0], MATCHES[1], BASE_GAPS[2], DISCREPANCY, EXPLANATION],
        id="a-single-month-matches",
    ),
    pytest.param(
        (edit("doc-202", net_pay="2000.00"),),
        [*BASE_GAPS, DISCREPANCY, EXPLANATION],
        id="payslip-below-the-credit",
    ),
    pytest.param(
        (edit("doc-208", recurring_bonus="1600.00"),),
        [*BASE_GAPS, EMPLOYER_MATCH, EXPLANATION],
        id="recurring-bonus-explains-declared-income",
    ),
    pytest.param(
        (edit("doc-208", monthly_net_pay="5000.00"),),
        [*BASE_GAPS, DISCREPANCY, EXPLANATION],
        id="employer-confirms-more-than-declared",
    ),
    pytest.param((drop("doc-209"),), [*BASE_GAPS, DISCREPANCY], id="no-explanation"),
    pytest.param(
        (copy("doc-209", "doc-909"),),
        [*BASE_GAPS, DISCREPANCY],
        id="two-explanations-are-ambiguous",
    ),
    pytest.param(
        (edit("doc-203", net_pay="bad"),),
        [
            BASE_GAPS[0],
            sig("gap:income_amount:doc-203", "doc-203", "doc-206"),
            BASE_GAPS[2],
            DISCREPANCY,
            EXPLANATION,
        ],
        id="payslip-amount-unreadable",
    ),
    pytest.param(
        (edit("doc-207", salary_credit="2600"),),
        [
            *BASE_GAPS[:2],
            sig("gap:income_amount:doc-204", "doc-204", "doc-207"),
            DISCREPANCY,
            EXPLANATION,
        ],
        id="credit-amount-without-cents",
    ),
]


@pytest.mark.parametrize(("edits", "expected"), CREDIT_ROWS)
def test_credit_signals_and_their_sources(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(102, CREDIT, edits) == expected


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param((edit("doc-201", employer_ref=""),), id="application-without-employer"),
        pytest.param((unverified("doc-208"),), id="employer-response-unverified"),
        pytest.param((unverified("doc-206"),), id="one-bank-extract-unverified"),
        pytest.param((edit("doc-203", employer_ref="DEMO-OTHER"),), id="payslip-other-employer"),
        pytest.param((edit("doc-206", employer_ref="DEMO-OTHER"),), id="extract-other-employer"),
        pytest.param(
            (edit("doc-208", employer_ref="DEMO-OTHER"),), id="confirmation-other-employer"
        ),
        pytest.param((edit("doc-201", currency="USD"),), id="application-currency"),
        pytest.param((edit("doc-208", currency="USD"),), id="employer-currency"),
        pytest.param((edit("doc-203", currency="USD"),), id="payslip-currency"),
        pytest.param((edit("doc-206", currency="USD"),), id="extract-currency"),
        pytest.param((edit("doc-201", declared_monthly_income="NaN"),), id="declared-income"),
        pytest.param((edit("doc-208", monthly_net_pay="2600"),), id="employer-net-pay"),
        pytest.param((edit("doc-208", recurring_bonus="1.5"),), id="employer-bonus"),
    ],
)
def test_one_income_inconsistency_is_enough_to_withhold_the_comparison(
    edits: tuple[Edit, ...],
) -> None:
    assert run(102, CREDIT, edits) == [INCOME_GAP]


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        pytest.param((edit("doc-203", month="2026-07"),), [PERIODS], id="repeated-payslip-month"),
        pytest.param((edit("doc-206", month="2026-07"),), [PERIODS], id="repeated-extract-month"),
        pytest.param(
            (edit("doc-204", month="2026-10"),), [PERIODS], id="payslip-month-without-extract"
        ),
        pytest.param(
            (edit("doc-204", month="2026-13"), edit("doc-207", month="2026-13")),
            [PERIODS],
            id="month-out-of-range",
        ),
        pytest.param(
            (edit("doc-204", month="2026-00"), edit("doc-207", month="2026-00")),
            [PERIODS],
            id="month-zero",
        ),
        pytest.param(
            (edit("doc-204", month="26-09"), edit("doc-207", month="26-09")),
            [PERIODS],
            id="short-year",
        ),
        pytest.param(
            (copy("doc-205", "doc-905"),),
            [PERIODS_WITH_COPIES],
            id="second-extract-for-the-same-month",
        ),
        pytest.param(
            (copy("doc-202", "doc-902"),),
            [
                sig(
                    "gap:income_periods",
                    "doc-202",
                    "doc-902",
                    "doc-203",
                    "doc-204",
                    "doc-205",
                    "doc-206",
                    "doc-207",
                )
            ],
            id="second-payslip-for-the-same-month",
        ),
        pytest.param(
            (drop("doc-204"),),
            [sig("gap:income_periods", "doc-202", "doc-203", "doc-205", "doc-206", "doc-207")],
            id="missing-payslip",
        ),
        pytest.param(
            (drop("doc-207"),),
            [sig("gap:income_periods", "doc-202", "doc-203", "doc-204", "doc-205", "doc-206")],
            id="missing-extract",
        ),
    ],
)
def test_unmatched_income_periods_cite_exactly_the_payslips_and_extracts_returned(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(102, CREDIT, edits) == expected


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param((drop("doc-201"),), id="no-application"),
        pytest.param((drop("doc-208"),), id="no-employer-response"),
        pytest.param((drop("doc-202"), drop("doc-203"), drop("doc-204")), id="no-payslips"),
        pytest.param((drop("doc-205"), drop("doc-206"), drop("doc-207")), id="no-extracts"),
        pytest.param((copy("doc-201", "doc-901"),), id="two-applications-are-ambiguous"),
        pytest.param((copy("doc-208", "doc-908"),), id="two-employer-responses-are-ambiguous"),
    ],
)
def test_incomplete_income_packages_cite_the_first_record_returned(edits: tuple[Edit, ...]) -> None:
    assert run(102, CREDIT, edits) == [sig("gap:income_documents", "kyc-102")]


LISTED = ("doc-301", "doc-302", "doc-303", "sanction-103", "kyc-103")
IDENTITY_GAP = sig("gap:identity_verification", *LISTED)
LATER = "1988-04-13"
OTHER_NAME = "Demo Other Name"

IDENTITY_ROWS = [
    pytest.param(
        (),
        [
            sig("context:registry_confirmation", "doc-301", "doc-303", "kyc-103"),
            sig("context:distinct_identity", *LISTED),
        ],
        id="different-birth-and-country",
    ),
    pytest.param(
        (edit("doc-302", date_of_birth="1988-04-12"),),
        [
            sig("context:registry_confirmation", "doc-301", "doc-303", "kyc-103"),
            sig("gap:partial_identity", *LISTED),
        ],
        id="same-birth-only",
    ),
    pytest.param(
        (edit("doc-302", country="FR"),),
        [
            sig("context:registry_confirmation", "doc-301", "doc-303", "kyc-103"),
            sig("gap:partial_identity", *LISTED),
        ],
        id="same-country-only",
    ),
    pytest.param(
        (edit("doc-302", date_of_birth="1988-04-12", country="FR"),),
        [
            sig("context:registry_confirmation", "doc-301", "doc-303", "kyc-103"),
            sig("finding:identity_match", *LISTED),
        ],
        id="same-birth-and-country",
    ),
]


@pytest.mark.parametrize(("edits", "expected"), IDENTITY_ROWS)
def test_identity_signals_and_their_sources(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(103, IDENTITY, edits) == expected


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param((edit("doc-301", date_of_birth="1988-13-45"),), id="customer-birth-invalid"),
        pytest.param((edit("doc-302", date_of_birth="1988-13-45"),), id="listed-birth-invalid"),
        pytest.param((edit("doc-303", date_of_birth="1988-13-45"),), id="registry-birth-invalid"),
        pytest.param((edit("doc-302", date_of_birth=""),), id="listed-birth-missing"),
        pytest.param((unverified("doc-301"),), id="identity-record-unverified"),
        pytest.param((unverified("doc-302"),), id="list-biography-unverified"),
        pytest.param((unverified("doc-303"),), id="registry-unverified"),
        pytest.param((edit("doc-302", sanction_record_id="sanction-999"),), id="other-list-entry"),
        pytest.param(
            (edit("doc-301", legal_name=OTHER_NAME), edit("doc-303", legal_name=OTHER_NAME)),
            id="customer-name-differs-from-kyc",
        ),
        pytest.param(
            (edit("doc-302", legal_name=OTHER_NAME),), id="biography-name-differs-from-entry"
        ),
        pytest.param(
            (edit("doc-301", document_ref=""), edit("doc-303", document_ref="")),
            id="document-reference-missing",
        ),
        pytest.param((edit("doc-303", legal_name=OTHER_NAME),), id="registry-name"),
        pytest.param((edit("doc-303", date_of_birth=LATER),), id="registry-birth-date"),
        pytest.param((edit("doc-303", country="DE"),), id="registry-country"),
        pytest.param((edit("doc-303", document_ref="DEMO-ID-OTHER"),), id="registry-document"),
        pytest.param(
            (edit("doc-301", country="fr"), edit("doc-303", country="fr")),
            id="customer-country-format",
        ),
        pytest.param((edit("doc-302", country="XZ1"),), id="listed-country-format"),
    ],
)
def test_one_identity_inconsistency_is_enough_to_withhold_the_comparison(
    edits: tuple[Edit, ...],
) -> None:
    assert run(103, IDENTITY, edits) == [IDENTITY_GAP]


def extra_profile(batches: Batches) -> Batches:
    return tuple(
        replace(
            batch,
            records=(
                *batch.records,
                *(
                    replace(r, record_id="kyc-903")
                    for r in batch.records
                    if isinstance(r, KycProfile)
                ),
            ),
        )
        for batch in batches
    )


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        pytest.param(
            (drop("doc-301"),), [sig("gap:identity_documents", "kyc-103")], id="no-identity"
        ),
        pytest.param(
            (drop("doc-302"),), [sig("gap:identity_documents", "kyc-103")], id="no-biography"
        ),
        pytest.param(
            (drop("doc-303"),), [sig("gap:identity_documents", "kyc-103")], id="no-registry"
        ),
        pytest.param(
            (copy("doc-303", "doc-903"),),
            [sig("gap:identity_documents", "kyc-103")],
            id="two-registry-checks-are-ambiguous",
        ),
        pytest.param(
            (drop("sanction-103"),), [sig("gap:identity_documents", "kyc-103")], id="no-list-entry"
        ),
        pytest.param(
            (copy("sanction-103", "sanction-903"),),
            [sig("gap:identity_documents", "kyc-103")],
            id="two-list-entries-are-ambiguous",
        ),
        pytest.param(
            (extra_profile,), [sig("gap:identity_documents", "kyc-103")], id="two-profiles"
        ),
        pytest.param(
            (drop("kyc-103"),), [sig("gap:identity_documents", "doc-301")], id="no-profile"
        ),
    ],
)
def test_incomplete_identity_packages_cite_the_first_record_returned(
    edits: tuple[Edit, ...], expected: list[Expected]
) -> None:
    assert run(103, IDENTITY, edits) == expected


def statements(number: int, kind: str, edits: tuple[Edit, ...] = ()) -> dict[str, str]:
    batches = evidence(number)
    for apply in edits:
        batches = apply(batches)
    return {signal.key: signal.statement for signal in banking_findings(batches, kind)}


def test_income_discrepancies_state_both_amounts_and_the_absolute_difference() -> None:
    below = statements(102, CREDIT, (edit("doc-202", net_pay="2000.00"),))
    text = below["finding:income_gap:doc-202"]
    assert "payslip states EUR 2000.00 while the bank records EUR 2600.00" in text
    assert "a EUR 600.00 discrepancy" in text
    above = statements(102, CREDIT)["finding:income_gap:doc-203"]
    assert "payslip states EUR 4200.00 while the bank records EUR 2600.00" in above
    assert "a EUR 1600.00 discrepancy" in above
    assert statements(102, CREDIT, CREDITS_4200)["context:income_match:doc-202"].endswith(
        "(EUR 4200.00)."
    )


@pytest.mark.parametrize(
    ("monthly", "bonus", "difference"),
    [
        ("2600.00", "0.00", "1600.00"),
        ("5000.00", "0.00", "800.00"),
        ("2600.00", "500.00", "1100.00"),
    ],
)
def test_employer_discrepancy_states_declared_confirmed_and_absolute_difference(
    monthly: str, bonus: str, difference: str
) -> None:
    text = statements(
        102, CREDIT, (edit("doc-208", monthly_net_pay=monthly, recurring_bonus=bonus),)
    )["finding:employer_discrepancy"]
    confirmed = Decimal(monthly) + Decimal(bonus)
    assert "Declared recurring monthly income is EUR 4200.00" in text
    assert f"confirms EUR {confirmed} including" in text
    assert f"the EUR {difference} difference" in text


def test_payment_amount_is_quoted_in_the_approved_purchase_context() -> None:
    assert (
        "The EUR 18450.00 payment matches" in statements(101, PAYMENT)["context:approved_purchase"]
    )


def test_distinct_identity_quotes_both_dates_and_countries() -> None:
    text = statements(103, IDENTITY)["context:distinct_identity"]
    assert "(1988-04-12 versus 1964-11-03)" in text and "(FR versus XZ)" in text
