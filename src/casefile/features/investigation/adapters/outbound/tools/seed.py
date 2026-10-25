"""Load a deterministic, entirely fictional snapshot bundled with the package."""

from importlib.resources import files
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from casefile.features.investigation.adapters.outbound.tools.wire import (
    AlertDocument,
    CaseDocumentRecord,
    Document,
    KycDocument,
    NoteDocument,
    SanctionDocument,
    TransactionDocument,
)


class ToolSeed(Document):
    schema_version: Literal[1]
    fictional: Literal[True]
    transactions: Annotated[tuple[TransactionDocument, ...], Field(max_length=100)]
    kyc: Annotated[tuple[KycDocument, ...], Field(max_length=100)]
    alerts: Annotated[tuple[AlertDocument, ...], Field(max_length=100)]
    sanctions: Annotated[tuple[SanctionDocument, ...], Field(max_length=100)]
    notes: Annotated[tuple[NoteDocument, ...], Field(max_length=100)]
    documents: Annotated[tuple[CaseDocumentRecord, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def check_references(self) -> Self:
        customers = {record.customer_id for record in self.kyc}
        if len(customers) != len(self.kyc):
            raise ValueError("Seed must contain at most one KYC profile per customer.")
        for records in (
            self.transactions,
            self.kyc,
            self.alerts,
            self.sanctions,
            self.notes,
            self.documents,
        ):
            if len({record.record_id for record in records}) != len(records):
                raise ValueError("Record identifiers must be unique within each tool.")
        for customer_records in (self.transactions, self.alerts, self.notes, self.documents):
            if any(record.customer_id not in customers for record in customer_records):
                raise ValueError("Seed records must reference a known fictional customer.")
        return self


def load_seed(*, banking: bool = False) -> ToolSeed:
    data = files(__package__).joinpath("seed.json").read_text(encoding="utf-8")
    seed = ToolSeed.model_validate_json(data)
    if not banking:
        return seed
    extra = ToolSeed.model_validate_json(
        files(__package__).joinpath("banking_seed.json").read_text(encoding="utf-8")
    )
    return ToolSeed(
        schema_version=1,
        fictional=True,
        transactions=seed.transactions + extra.transactions,
        kyc=seed.kyc + extra.kyc,
        alerts=seed.alerts + extra.alerts,
        sanctions=seed.sanctions + extra.sanctions,
        notes=seed.notes + extra.notes,
        documents=seed.documents + extra.documents,
    )
