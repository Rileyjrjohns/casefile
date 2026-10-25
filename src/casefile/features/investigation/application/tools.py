"""Typed, read-only evidence ports, independent of MCP and validation frameworks."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from casefile.shared.result import Err, Ok, Result


class ToolName(StrEnum):
    TRANSACTIONS = "list_transactions"
    KYC = "get_kyc_profile"
    ALERTS = "list_alerts"
    SANCTIONS = "search_sanctions"
    NOTES = "list_investigator_notes"
    DOCUMENTS = "list_case_documents"


@dataclass(frozen=True, slots=True)
class InvalidToolQuery:
    message: str


type ToolResult[T] = Result[tuple[T, ...], InvalidToolQuery]


def customer_query(value: str) -> Result[str, InvalidToolQuery]:
    if re.fullmatch(r"demo-customer-[0-9]{3}", value) is None:
        return Err(InvalidToolQuery("Expected a fictional demo-customer-NNN identifier."))
    return Ok(value)


def sanction_query(value: str) -> Result[str, InvalidToolQuery]:
    if not value.strip() or len(value) > 120 or any(ord(char) < 32 for char in value):
        return Err(InvalidToolQuery("A name needs 1-120 characters without control characters."))
    return Ok(" ".join(value.split()).casefold())


@dataclass(frozen=True, slots=True)
class Transaction:
    record_id: str
    customer_id: str
    booked_at: datetime
    amount: Decimal
    currency: str
    counterparty: str
    country: str


@dataclass(frozen=True, slots=True)
class KycProfile:
    record_id: str
    customer_id: str
    legal_name: str
    country: str
    occupation: str
    expected_monthly_volume: Decimal
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class AlertRecord:
    record_id: str
    customer_id: str
    kind: str
    status: str
    created_at: datetime
    summary: str


@dataclass(frozen=True, slots=True)
class SanctionEntry:
    record_id: str
    name: str
    aliases: tuple[str, ...]
    program: str
    listed_at: date


@dataclass(frozen=True, slots=True)
class InvestigatorNote:
    record_id: str
    customer_id: str
    author: str
    text: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceField:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class CaseDocument:
    record_id: str
    customer_id: str
    kind: str
    title: str
    issuer: str
    issued_at: datetime
    verification: str
    fields: tuple[EvidenceField, ...]
    text: str


class TransactionsTool(Protocol):
    async def list_transactions(self, customer_id: str) -> ToolResult[Transaction]: ...


class KycTool(Protocol):
    async def get_kyc_profile(self, customer_id: str) -> ToolResult[KycProfile]: ...


class AlertsTool(Protocol):
    async def list_alerts(self, customer_id: str) -> ToolResult[AlertRecord]: ...


class SanctionsTool(Protocol):
    async def search_sanctions(self, name: str) -> ToolResult[SanctionEntry]: ...


class NotesTool(Protocol):
    async def list_investigator_notes(self, customer_id: str) -> ToolResult[InvestigatorNote]: ...


class DocumentsTool(Protocol):
    async def list_case_documents(self, customer_id: str) -> ToolResult[CaseDocument]: ...


class InvestigationTools(
    TransactionsTool, KycTool, AlertsTool, SanctionsTool, NotesTool, DocumentsTool, Protocol
):
    """A composition convenience; each tool remains an independently injectable port."""
