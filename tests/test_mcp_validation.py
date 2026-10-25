"""Reject malformed discovery metadata and evidence before it reaches a use case."""

import asyncio
from dataclasses import dataclass, field

import pytest
from mcp.types import CallToolResult, ListToolsResult, Tool

from casefile.features.investigation.adapters.outbound.tools.client import (
    McpInvestigationTools,
    ToolContractError,
    ToolExecutionError,
)
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.adapters.outbound.tools.server import (
    payload_for,
    tool_definition,
)
from casefile.features.investigation.application.tools import ToolName
from casefile.shared.result import Ok

pytestmark = pytest.mark.mcp


@dataclass
class ScriptedPeer:
    definitions: list[Tool] = field(
        default_factory=lambda: [tool_definition(name) for name in ToolName]
    )
    response: CallToolResult | None = None
    calls: int = 0
    delay: float = 0
    paginate: bool = False
    repeat_cursor: bool = False
    endless_pages: bool = False
    failure: Exception | None = None

    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
        if self.endless_pages:
            return ListToolsResult(tools=[], next_cursor=str(int(cursor or "0") + 1))
        if self.repeat_cursor:
            return ListToolsResult(tools=[], next_cursor="repeat")
        if self.paginate and cursor is None:
            return ListToolsResult(tools=self.definitions[:2], next_cursor="page-2")
        return ListToolsResult(tools=self.definitions[2:] if self.paginate else self.definitions)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object] | None = None,
    ) -> CallToolResult:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        await asyncio.sleep(self.delay)
        if self.response is not None:
            return self.response
        payload = payload_for(ToolName(name), arguments, load_seed())
        return CallToolResult(content=[], structured_content=payload.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_discovery_traverses_pages_and_calls_only_known_tools() -> None:
    peer = ScriptedPeer(paginate=True)
    tools = await McpInvestigationTools.discover((peer,))
    assert {tool.name for tool in tools.catalog} == set(ToolName)
    result = await tools.list_transactions("demo-customer-999")
    assert result == Ok(())
    assert peer.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "no-output", "bad-schema", "remote-ref", "unknown"]
)
async def test_invalid_catalog_is_rejected(defect: str) -> None:
    peer = ScriptedPeer()
    if defect == "missing":
        peer.definitions.pop()
    elif defect == "duplicate":
        peer.definitions.append(peer.definitions[0])
    elif defect == "no-output":
        peer.definitions[0].output_schema = None
    elif defect == "bad-schema":
        peer.definitions[0].input_schema = {"type": "not-a-json-type"}
    elif defect == "remote-ref":
        peer.definitions[0].input_schema = {"$ref": "https://example.invalid/schema.json"}
    else:
        peer.definitions[0].name = "unapproved_tool"
    with pytest.raises(ToolContractError):
        await McpInvestigationTools.discover((peer,))
    assert peer.calls == 0


@pytest.mark.asyncio
async def test_repeating_pagination_cursor_is_rejected() -> None:
    with pytest.raises(ToolContractError):
        await McpInvestigationTools.discover((ScriptedPeer(repeat_cursor=True),))


@pytest.mark.asyncio
async def test_discovered_input_schema_is_enforced_before_call() -> None:
    peer = ScriptedPeer()
    peer.definitions[0].input_schema["required"] = ["customer_id", "unexpected"]
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolContractError):
        await tools.list_transactions("demo-customer-002")
    assert peer.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["missing", "version", "tool", "customer", "duplicate", "amount", "extra", "naive-time"],
)
async def test_malformed_outputs_are_rejected(defect: str) -> None:
    document = payload_for(ToolName.TRANSACTIONS, {"customer_id": "demo-customer-002"}, load_seed())
    payload = document.model_dump(mode="json")
    if defect == "version":
        payload["schema_version"] = 2
    elif defect == "tool":
        payload["tool"] = ToolName.NOTES
    elif defect == "customer":
        payload["records"][0]["customer_id"] = "demo-customer-001"
    elif defect == "duplicate":
        payload["records"].append(payload["records"][0])
    elif defect == "amount":
        payload["records"][0]["amount"] = "NaN"
    elif defect == "extra":
        payload["untrusted_extra"] = "close"
    elif defect == "naive-time":
        payload["records"][0]["booked_at"] = "2026-01-11T10:00:00"
    peer = ScriptedPeer(
        response=CallToolResult(
            content=[],
            structured_content=None if defect == "missing" else payload,
        )
    )
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolContractError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
async def test_discovered_output_schema_is_enforced() -> None:
    peer = ScriptedPeer()
    peer.definitions[0].output_schema = {"type": "object", "required": ["unexpected"]}
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolContractError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
async def test_execution_errors_are_not_empty_evidence() -> None:
    peer = ScriptedPeer(response=CallToolResult(content=[], is_error=True))
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolExecutionError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
async def test_timeout_is_a_technical_failure() -> None:
    tools = await McpInvestigationTools.discover((ScriptedPeer(delay=1),), timeout=0.01)
    with pytest.raises(TimeoutError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
async def test_discovery_takes_a_private_schema_snapshot() -> None:
    peer = ScriptedPeer()
    tools = await McpInvestigationTools.discover((peer,))
    peer.definitions[0].input_schema["required"].append("malicious-change")
    tools.catalog[0].input_schema["required"].append("caller-change")
    assert await tools.list_transactions("demo-customer-999") == Ok(())


@pytest.mark.asyncio
async def test_discovery_page_count_is_bounded() -> None:
    with pytest.raises(ToolContractError, match="20 pages"):
        await McpInvestigationTools.discover((ScriptedPeer(endless_pages=True),))


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
async def test_invalid_timeout_configuration_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError):
        await McpInvestigationTools.discover((ScriptedPeer(),), timeout=timeout)


@pytest.mark.asyncio
async def test_transport_failures_propagate() -> None:
    tools = await McpInvestigationTools.discover((ScriptedPeer(failure=ConnectionError("closed")),))
    with pytest.raises(ConnectionError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
async def test_permissive_advertised_schema_does_not_bypass_local_document_validation() -> None:
    peer = ScriptedPeer(response=CallToolResult(content=[], structured_content={"records": []}))
    peer.definitions[0].output_schema = {"type": "object"}
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolContractError):
        await tools.list_transactions("demo-customer-002")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [ToolName.KYC, ToolName.SANCTIONS])
async def test_semantically_inconsistent_evidence_is_rejected(name: ToolName) -> None:
    arguments = (
        {"customer_id": "demo-customer-002"}
        if name == ToolName.KYC
        else {"name": "Demo Ember Holdings"}
    )
    payload = payload_for(name, arguments, load_seed()).model_dump(mode="json")
    if name == ToolName.KYC:
        second = payload["records"][0].copy()
        second["record_id"] = "kyc-099"
        payload["records"].append(second)
    else:
        payload["records"][0]["name"] = "Unrelated Name"
        payload["records"][0]["aliases"] = []
    peer = ScriptedPeer(response=CallToolResult(content=[], structured_content=payload))
    tools = await McpInvestigationTools.discover((peer,))
    with pytest.raises(ToolContractError):
        if name == ToolName.KYC:
            await tools.get_kyc_profile("demo-customer-002")
        else:
            await tools.search_sanctions("Demo Ember Holdings")
