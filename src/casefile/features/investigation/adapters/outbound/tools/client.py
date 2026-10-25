"""Discover MCP schemas and validate evidence before crossing into the application."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonValidationError
from mcp.types import CallToolResult, ListToolsResult, Tool
from pydantic import ValidationError

from casefile.features.investigation.adapters.outbound.tools.wire import (
    AlertDocument,
    CaseDocumentRecord,
    CustomerRecordDocument,
    KycDocument,
    NoteDocument,
    RecordDocument,
    Records,
    SanctionDocument,
    TransactionDocument,
)
from casefile.features.investigation.application.tools import (
    AlertRecord,
    CaseDocument,
    InvalidToolQuery,
    InvestigatorNote,
    KycProfile,
    SanctionEntry,
    ToolName,
    ToolResult,
    Transaction,
    customer_query,
    sanction_query,
)
from casefile.shared.result import Err, Ok, Result


class ToolContractError(RuntimeError):
    """A provider advertised an incompatible schema or returned invalid evidence."""


class ToolExecutionError(RuntimeError):
    """A tool reported failure; this must never be treated as an empty result."""


class McpPeer(Protocol):
    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object] | None = None,
    ) -> CallToolResult: ...


def _check_local_references(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("$ref", "$dynamicRef") and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise ToolContractError("Tool schemas may only use local JSON references.")
            _check_local_references(child)
    elif isinstance(value, list):
        for child in value:
            _check_local_references(child)


def _validator(schema: dict[str, object]) -> Draft202012Validator:
    _check_local_references(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ToolContractError("Invalid tool JSON Schema.") from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


@dataclass(frozen=True)
class Binding:
    peer: McpPeer
    definition: Tool
    input_validator: Draft202012Validator
    output_validator: Draft202012Validator


class McpInvestigationTools:
    def __init__(self, bindings: dict[ToolName, Binding], timeout: float) -> None:
        self._bindings = bindings.copy()
        self._timeout = timeout

    @property
    def catalog(self) -> tuple[Tool, ...]:
        return tuple(
            binding.definition.model_copy(deep=True) for binding in self._bindings.values()
        )

    @classmethod
    async def discover(
        cls,
        peers: Sequence[McpPeer],
        *,
        timeout: float = 5,
    ) -> "McpInvestigationTools":
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("Tool timeout must be positive and finite.")
        bindings: dict[ToolName, Binding] = {}
        for peer in peers:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for _ in range(20):
                async with asyncio.timeout(timeout):
                    page = await peer.list_tools(cursor=cursor)
                for item in page.tools:
                    definition = item.model_copy(deep=True)
                    try:
                        name = ToolName(definition.name)
                    except ValueError as error:
                        raise ToolContractError(
                            "Provider advertised an unapproved tool."
                        ) from error
                    if name in bindings or definition.output_schema is None:
                        raise ToolContractError("Duplicate tool or missing output schema.")
                    bindings[name] = Binding(
                        peer,
                        definition,
                        _validator(definition.input_schema),
                        _validator(definition.output_schema),
                    )
                cursor = page.next_cursor
                if cursor is None:
                    break
                if cursor in seen_cursors:
                    raise ToolContractError("Repeated tools/list cursor.")
                seen_cursors.add(cursor)
            else:
                raise ToolContractError("Tool discovery exceeded 20 pages.")
        if set(bindings) != set(ToolName):
            raise ToolContractError("The required evidence tools were not discovered.")
        return cls(bindings, timeout)

    async def _read[D: RecordDocument, T](
        self,
        name: ToolName,
        key: str,
        query: Result[str, InvalidToolQuery],
        model: type[Records[D]],
        convert: Callable[[D], T],
    ) -> ToolResult[T]:
        if isinstance(query, Err):
            return query
        arguments: dict[str, object] = {key: query.value}
        binding = self._bindings[name]
        try:
            binding.input_validator.validate(arguments)
        except JsonValidationError as error:
            raise ToolContractError(f"Input schema mismatch for {name}.") from error
        async with asyncio.timeout(self._timeout):
            result = await binding.peer.call_tool(name, arguments)
        if result.is_error:
            raise ToolExecutionError(f"Evidence tool {name} failed.")
        if not isinstance(result.structured_content, dict):
            raise ToolContractError(f"Missing structured content from {name}.")
        try:
            binding.output_validator.validate(result.structured_content)
            payload = model.model_validate(result.structured_content)
        except (JsonValidationError, ValidationError) as error:
            raise ToolContractError(f"Invalid evidence from {name}.") from error
        if payload.tool != name:
            raise ToolContractError("Evidence identifies a different tool.")
        if len({record.record_id for record in payload.records}) != len(payload.records):
            raise ToolContractError("Evidence contains duplicate record identifiers.")
        if name == ToolName.KYC and len(payload.records) > 1:
            raise ToolContractError("KYC returned multiple profiles for one customer.")
        for record in payload.records:
            if isinstance(record, CustomerRecordDocument) and record.customer_id != query.value:
                raise ToolContractError("Evidence belongs to another customer.")
            if isinstance(record, SanctionDocument) and not any(
                " ".join(alias.split()).casefold() == query.value
                for alias in (record.name, *record.aliases)
            ):
                raise ToolContractError("Sanction evidence does not match the queried name.")
        return Ok(tuple(convert(record) for record in payload.records))

    async def list_transactions(self, customer_id: str) -> ToolResult[Transaction]:
        return await self._read(
            ToolName.TRANSACTIONS,
            "customer_id",
            customer_query(customer_id),
            Records[TransactionDocument],
            lambda record: record.to_record(),
        )

    async def get_kyc_profile(self, customer_id: str) -> ToolResult[KycProfile]:
        return await self._read(
            ToolName.KYC,
            "customer_id",
            customer_query(customer_id),
            Records[KycDocument],
            lambda record: record.to_record(),
        )

    async def list_alerts(self, customer_id: str) -> ToolResult[AlertRecord]:
        return await self._read(
            ToolName.ALERTS,
            "customer_id",
            customer_query(customer_id),
            Records[AlertDocument],
            lambda record: record.to_record(),
        )

    async def search_sanctions(self, name: str) -> ToolResult[SanctionEntry]:
        return await self._read(
            ToolName.SANCTIONS,
            "name",
            sanction_query(name),
            Records[SanctionDocument],
            lambda record: record.to_record(),
        )

    async def list_investigator_notes(self, customer_id: str) -> ToolResult[InvestigatorNote]:
        return await self._read(
            ToolName.NOTES,
            "customer_id",
            customer_query(customer_id),
            Records[NoteDocument],
            lambda record: record.to_record(),
        )

    async def list_case_documents(self, customer_id: str) -> ToolResult[CaseDocument]:
        return await self._read(
            ToolName.DOCUMENTS,
            "customer_id",
            customer_query(customer_id),
            Records[CaseDocumentRecord],
            lambda record: record.to_record(),
        )
