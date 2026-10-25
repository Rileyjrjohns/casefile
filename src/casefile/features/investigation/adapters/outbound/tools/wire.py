"""Strict, versioned documents used only at the evidence integration boundary."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from casefile.features.investigation.application.tools import (
    AlertRecord,
    CaseDocument,
    EvidenceField,
    InvestigatorNote,
    KycProfile,
    SanctionEntry,
    ToolName,
    Transaction,
)

type Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z]+-[0-9]{3}$", strict=True)]
type CustomerId = Annotated[
    str, StringConstraints(pattern=r"^demo-customer-[0-9]{3}$", strict=True)
]
type Text = Annotated[str, StringConstraints(min_length=1, max_length=2000, strict=True)]
type Name = Annotated[str, StringConstraints(min_length=1, max_length=120, strict=True)]
type Money = Annotated[
    str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,11})\.[0-9]{2}$", strict=True)
]
type Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$", strict=True)]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerQuery(Document):
    customer_id: CustomerId


class SanctionQuery(Document):
    name: Annotated[Name, Field(pattern=r"^[^\x00-\x1f]*\S[^\x00-\x1f]*$")]


class RecordDocument(Document):
    record_id: Identifier


class CustomerRecordDocument(RecordDocument):
    customer_id: CustomerId


class TransactionDocument(CustomerRecordDocument):
    booked_at: AwareDatetime
    amount: Money
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$", strict=True)]
    counterparty: Name
    country: Country

    def to_record(self) -> Transaction:
        return Transaction(
            self.record_id,
            self.customer_id,
            self.booked_at,
            Decimal(self.amount),
            self.currency,
            self.counterparty,
            self.country,
        )


class KycDocument(CustomerRecordDocument):
    legal_name: Name
    country: Country
    occupation: Text
    expected_monthly_volume: Money
    reviewed_at: AwareDatetime

    def to_record(self) -> KycProfile:
        return KycProfile(
            self.record_id,
            self.customer_id,
            self.legal_name,
            self.country,
            self.occupation,
            Decimal(self.expected_monthly_volume),
            self.reviewed_at,
        )


class AlertDocument(CustomerRecordDocument):
    kind: Literal["unusual_transaction", "sanctions_match", "kyc_mismatch", "document_mismatch"]
    status: Literal["open", "closed"]
    created_at: AwareDatetime
    summary: Text

    def to_record(self) -> AlertRecord:
        return AlertRecord(
            self.record_id,
            self.customer_id,
            self.kind,
            self.status,
            self.created_at,
            self.summary,
        )


class SanctionDocument(RecordDocument):
    name: Name
    aliases: Annotated[tuple[Name, ...], Field(max_length=10)]
    program: Name
    listed_at: date

    def to_record(self) -> SanctionEntry:
        return SanctionEntry(self.record_id, self.name, self.aliases, self.program, self.listed_at)


class NoteDocument(CustomerRecordDocument):
    author: Name
    text: Text
    created_at: AwareDatetime

    def to_record(self) -> InvestigatorNote:
        return InvestigatorNote(
            self.record_id,
            self.customer_id,
            self.author,
            self.text,
            self.created_at,
        )


class EvidenceFieldDocument(Document):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{0,49}$", strict=True)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=500, strict=True)]


class CaseDocumentRecord(CustomerRecordDocument):
    kind: Literal[
        "invoice",
        "payment_instruction",
        "supplier_confirmation",
        "purchase_order",
        "credit_application",
        "payslip",
        "bank_statement",
        "employment_confirmation",
        "identity_record",
        "watchlist_identity",
        "registry_confirmation",
        "customer_explanation",
    ]
    title: Name
    issuer: Name
    issued_at: AwareDatetime
    verification: Literal["unverified", "independent"]
    fields: Annotated[tuple[EvidenceFieldDocument, ...], Field(min_length=1, max_length=20)]
    text: Text

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Document fields must have unique names.")
        return self

    def to_record(self) -> CaseDocument:
        return CaseDocument(
            self.record_id,
            self.customer_id,
            self.kind,
            self.title,
            self.issuer,
            self.issued_at,
            self.verification,
            tuple(EvidenceField(field.name, field.value) for field in self.fields),
            self.text,
        )


class Records[D: RecordDocument](Document):
    schema_version: Literal[1]
    tool: ToolName
    records: Annotated[tuple[D, ...], Field(max_length=100)]
