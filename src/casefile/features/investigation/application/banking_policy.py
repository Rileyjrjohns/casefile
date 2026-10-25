"""Compare structured fictional records without deciding guilt or credit eligibility."""

import re
from datetime import date
from decimal import Decimal

from casefile.features.investigation.application.agent_types import (
    EvidenceBatch,
    Signal,
    SourceLink,
)
from casefile.features.investigation.application.tools import (
    CaseDocument,
    KycProfile,
    SanctionEntry,
    ToolName,
)

BANKING_KINDS = frozenset({"payment_fraud", "credit_document_fraud", "sanctions_identity_review"})


def is_concern(signal: Signal) -> bool:
    return not signal.key.startswith(("context:", "gap:"))


def fields(record: CaseDocument) -> dict[str, str]:
    return {field.name: field.value for field in record.fields}


def amount(value: str) -> Decimal | None:
    return Decimal(value) if re.fullmatch(r"(0|[1-9][0-9]{0,11})\.[0-9]{2}", value) else None


class Dossier:
    def __init__(self, evidence: tuple[EvidenceBatch, ...]) -> None:
        self.documents = tuple(
            record
            for batch in evidence
            for record in batch.records
            if isinstance(record, CaseDocument)
        )
        self.sanctions = tuple(
            record
            for batch in evidence
            for record in batch.records
            if isinstance(record, SanctionEntry)
        )
        self.profiles = tuple(
            record
            for batch in evidence
            for record in batch.records
            if isinstance(record, KycProfile)
        )
        self.fallback = tuple(
            SourceLink(batch.tool, record.record_id)
            for batch in evidence
            for record in batch.records
        )[:1]
        self.signals: list[Signal] = []

    def one(self, kind: str) -> CaseDocument | None:
        matches = tuple(doc for doc in self.documents if doc.kind == kind)
        return matches[0] if len(matches) == 1 else None

    def add(
        self, key: str, text: str, *documents: CaseDocument, extra: tuple[SourceLink, ...] = ()
    ) -> None:
        sources = (
            tuple(
                dict.fromkeys(
                    (*[SourceLink(ToolName.DOCUMENTS, doc.record_id) for doc in documents], *extra)
                )
            )
            or self.fallback
        )
        if sources:
            self.signals.append(Signal(key, text, 1.0, sources))

    def payment(self) -> None:
        invoice, order = self.one("invoice"), self.one("purchase_order")
        instruction, confirmation = (
            self.one("payment_instruction"),
            self.one("supplier_confirmation"),
        )
        if not invoice or not order or not instruction or not confirmation:
            self.add(
                "gap:payment_documents",
                "The payment package is incomplete or ambiguous; obtain the original invoice, "
                "order, instruction and independent supplier confirmation.",
            )
            return
        inv, po, ins, conf = (fields(doc) for doc in (invoice, order, instruction, confirmation))
        if (
            any(doc.verification != "independent" for doc in (invoice, order, confirmation))
            or not inv.get("invoice_ref")
            or not inv.get("purchase_order_ref")
            or inv["invoice_ref"] != ins.get("invoice_ref")
            or inv["invoice_ref"] != conf.get("invoice_ref")
            or inv["purchase_order_ref"] != po.get("purchase_order_ref")
            or len({inv.get("currency"), po.get("currency"), ins.get("currency")}) != 1
            or inv.get("currency") != "EUR"
            or any(amount(doc.get("amount", "")) is None for doc in (inv, po, ins))
            or not all(doc.get("beneficiary_account") for doc in (inv, ins, conf))
            or conf.get("change_authorized") not in {"yes", "no"}
        ):
            self.add(
                "gap:payment_verification",
                "Document references, amounts, currencies or independent verification do not "
                "support a reliable payment comparison.",
                invoice,
                order,
                instruction,
                confirmation,
            )
            return
        if inv["amount"] == po["amount"] == ins["amount"]:
            self.add(
                "context:approved_purchase",
                f"The EUR {inv['amount']} payment matches the approved order and original invoice; "
                f"the amount alone does not establish fraud.",
                invoice,
                order,
                instruction,
            )
        else:
            self.add(
                "finding:payment_amount",
                "The payment, original invoice and approved order show different amounts; verify "
                "the discrepancy before accepting the payment explanation.",
                invoice,
                order,
                instruction,
            )
        if ins["beneficiary_account"] == inv["beneficiary_account"]:
            self.add(
                "context:unchanged_account",
                "The instructed beneficiary account matches the original invoice.",
                instruction,
                invoice,
            )
        elif (
            conf["change_authorized"] == "yes"
            and conf["beneficiary_account"] == ins["beneficiary_account"]
        ):
            self.add(
                "context:confirmed_account_change",
                "The independently contacted supplier confirms the new beneficiary account; the "
                "change alone does not establish fraud.",
                instruction,
                confirmation,
                invoice,
            )
        else:
            self.add(
                "finding:beneficiary_mismatch",
                "The instructed beneficiary account differs from the original invoice and is not "
                "confirmed by the independent supplier response; suspected payment diversion "
                "requires investigation.",
                instruction,
                invoice,
                confirmation,
            )
        if (
            conf["change_authorized"] == "no"
            and ins["beneficiary_account"] != conf["beneficiary_account"]
        ):
            self.add(
                "finding:supplier_denial",
                "The supplier confirms the invoice but denies authorizing the requested account "
                "change through the recorded independent contact.",
                confirmation,
                instruction,
            )

    def credit(self) -> None:
        application, employer = self.one("credit_application"), self.one("employment_confirmation")
        payslips = tuple(doc for doc in self.documents if doc.kind == "payslip")
        statements = tuple(doc for doc in self.documents if doc.kind == "bank_statement")
        if not application or not employer or not payslips or not statements:
            self.add(
                "gap:income_documents",
                "Income verification is incomplete: obtain the application, payslips, bank "
                "salary-credit extracts and an independent employer response.",
            )
            return
        app, emp = fields(application), fields(employer)
        checked = (application, employer, *payslips, *statements)
        if (
            not app.get("employer_ref")
            or employer.verification != "independent"
            or any(doc.verification != "independent" for doc in statements)
            or any(fields(doc).get("employer_ref") != app["employer_ref"] for doc in checked)
            or any(fields(doc).get("currency") != "EUR" for doc in checked)
            or amount(app.get("declared_monthly_income", "")) is None
            or amount(emp.get("monthly_net_pay", "")) is None
            or amount(emp.get("recurring_bonus", "")) is None
        ):
            self.add(
                "gap:income_verification",
                "Employer references, currency, income amounts or source verification are "
                "inconsistent; do not compare unrelated income records.",
                *checked,
            )
            return
        months = [fields(doc).get("month", "") for doc in payslips]
        bank_months = [fields(doc).get("month", "") for doc in statements]
        if (
            len(set(months)) != len(months)
            or len(set(bank_months)) != len(bank_months)
            or set(months) != set(bank_months)
            or any(re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) is None for month in months)
        ):
            self.add(
                "gap:income_periods",
                "Payslips and salary credits do not provide unique matching monthly periods; "
                "request the missing or corrected extracts.",
                *payslips,
                *statements,
            )
            return
        for slip in payslips:
            values = fields(slip)
            bank = next(doc for doc in statements if fields(doc)["month"] == values["month"])
            paid, credited = (
                amount(values.get("net_pay", "")),
                amount(fields(bank).get("salary_credit", "")),
            )
            if paid is None or credited is None:
                self.add(
                    f"gap:income_amount:{slip.record_id}",
                    "A payslip or salary-credit amount cannot be compared; "
                    "request a valid extract.",
                    slip,
                    bank,
                )
                continue
            if paid != credited:
                self.add(
                    f"finding:income_gap:{slip.record_id}",
                    f"For {values['month']}, the submitted payslip states EUR {paid} while the "
                    f"bank records EUR {credited} from the same employer: a EUR "
                    f"{abs(paid - credited)} discrepancy requiring explanation, "
                    "not proof of fraud.",
                    slip,
                    bank,
                )
            else:
                self.add(
                    f"context:income_match:{slip.record_id}",
                    f"For {values['month']}, the payslip amount matches the recorded salary credit "
                    f"(EUR {paid}).",
                    slip,
                    bank,
                )
        declared = Decimal(app["declared_monthly_income"])
        confirmed = Decimal(emp["monthly_net_pay"]) + Decimal(emp["recurring_bonus"])
        if declared != confirmed:
            self.add(
                "finding:employer_discrepancy",
                f"Declared recurring monthly income is EUR {declared}; the independently contacted "
                f"employer confirms EUR {confirmed} including recurring bonuses. Reconcile the EUR "
                f"{abs(declared - confirmed)} difference before relying on the submitted income "
                f"documents.",
                application,
                employer,
            )
        else:
            self.add(
                "context:employer_confirmation",
                "The declared recurring income matches the independently recorded employer "
                "confirmation.",
                application,
                employer,
            )
        explanation = self.one("customer_explanation")
        if explanation:
            self.add(
                "context:applicant_explanation",
                "The applicant attributes additional credits to bonuses or reimbursements. This "
                "recorded explanation does not independently verify recurring salary and must be "
                "considered alongside the employer response.",
                explanation,
                employer,
            )

    def identity(self) -> None:
        identity, biography, registry = (
            self.one("identity_record"),
            self.one("watchlist_identity"),
            self.one("registry_confirmation"),
        )
        if (
            not identity
            or not biography
            or not registry
            or len(self.sanctions) != 1
            or len(self.profiles) != 1
        ):
            self.add(
                "gap:identity_documents",
                "The name-only alert needs an unambiguous list entry, its biography, the customer "
                "identity record and an independent registry check.",
            )
            return
        customer, listed, verified = (fields(doc) for doc in (identity, biography, registry))
        sanction, profile = self.sanctions[0], self.profiles[0]
        extra = (
            SourceLink(ToolName.SANCTIONS, sanction.record_id),
            SourceLink(ToolName.KYC, profile.record_id),
        )
        valid_dates = True
        try:
            for doc in (customer, listed, verified):
                date.fromisoformat(doc.get("date_of_birth", ""))
        except ValueError:
            valid_dates = False
        if (
            not valid_dates
            or any(doc.verification != "independent" for doc in (identity, biography, registry))
            or listed.get("sanction_record_id") != sanction.record_id
            or customer.get("legal_name") != profile.legal_name
            or listed.get("legal_name") != sanction.name
            or not customer.get("document_ref")
            or any(
                customer.get(key) != verified.get(key)
                for key in ("legal_name", "date_of_birth", "country", "document_ref")
            )
            or any(
                re.fullmatch(r"[A-Z]{2}", doc.get("country", "")) is None
                for doc in (customer, listed)
            )
        ):
            self.add(
                "gap:identity_verification",
                "The identity comparisons are incomplete, inconsistent or unverified; do not clear "
                "or confirm the screening hit from the name alone.",
                identity,
                biography,
                registry,
                extra=extra,
            )
            return
        self.add(
            "context:registry_confirmation",
            "The independent registry matches the customer's identity document and KYC name; this "
            "establishes the identity being compared, not a sanctions decision.",
            identity,
            registry,
            extra=(SourceLink(ToolName.KYC, profile.record_id),),
        )
        same_birth = customer["date_of_birth"] == listed["date_of_birth"]
        same_country = customer["country"] == listed["country"]
        if not same_birth and not same_country:
            self.add(
                "context:distinct_identity",
                f"The matching name belongs to records with different dates of birth "
                f"({customer['date_of_birth']} versus {listed['date_of_birth']}) and countries "
                f"({customer['country']} versus {listed['country']}). The independent identity "
                f"check supports a false-positive hypothesis for human review; the name alone does "
                f"not establish a match.",
                identity,
                biography,
                registry,
                extra=extra,
            )
        elif same_birth and same_country:
            self.add(
                "finding:identity_match",
                "Name, birth date and country match the fictional listed biography; escalate for "
                "human identity verification without treating this as proof "
                "of a legal restriction.",
                identity,
                biography,
                registry,
                extra=extra,
            )
        else:
            self.add(
                "gap:partial_identity",
                "Some biographical fields match and others differ; obtain additional identifiers "
                "before clearing or confirming the name-only screening alert.",
                identity,
                biography,
                registry,
                extra=extra,
            )


def banking_findings(evidence: tuple[EvidenceBatch, ...], kind: str) -> tuple[Signal, ...]:
    dossier = Dossier(evidence)
    if kind == "payment_fraud":
        dossier.payment()
    elif kind == "credit_document_fraud":
        dossier.credit()
    elif kind == "sanctions_identity_review":
        dossier.identity()
    return tuple(dossier.signals)
