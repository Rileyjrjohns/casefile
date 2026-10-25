"""Exercise the SDK protocol in-process and against all evidence subprocesses."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent

from casefile.bootstrap.tools import connect_demo_clients
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.adapters.outbound.tools.server import build_provider
from casefile.features.investigation.application.tools import ToolName

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["in-process", "stdio"], scope="module")
async def peers(request: pytest.FixtureRequest) -> AsyncIterator[tuple[Client, ...]]:
    if request.param == "stdio":
        async with connect_demo_clients() as clients:
            yield clients
    else:
        async with AsyncExitStack() as stack:
            clients = tuple(
                [
                    await stack.enter_async_context(Client(build_provider(name, load_seed())))
                    for name in ToolName
                ]
            )
            yield clients


async def test_discovery_schemas_and_structured_results(peers: tuple[Client, ...]) -> None:
    for peer, expected in zip(peers, ToolName, strict=True):
        tools = (await peer.list_tools()).tools
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == expected
        assert tool.annotations is not None and tool.annotations.read_only_hint
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema is not None
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
        arguments = (
            {"name": "Demo Ember Holdings"}
            if expected == ToolName.SANCTIONS
            else {"customer_id": "demo-customer-002"}
        )
        result = await peer.call_tool(tool.name, arguments)
        assert not result.is_error
        assert isinstance(result.structured_content, dict)
        Draft202012Validator(tool.output_schema, format_checker=FormatChecker()).validate(
            result.structured_content,
        )
        assert result.structured_content["schema_version"] == 1
        assert result.structured_content["tool"] == expected
        assert len(result.structured_content["records"]) == (
            0 if expected == ToolName.DOCUMENTS else 1
        )
        block = result.content[0]
        assert isinstance(block, TextContent)
        assert json.loads(block.text) == result.structured_content
        if expected == ToolName.TRANSACTIONS:
            assert result.structured_content["records"][0]["amount"] == "27500.75"


@pytest.mark.parametrize("defect", ["missing", "wrong-type", "blank", "extra", "control"])
async def test_invalid_arguments_are_reported(peers: tuple[Client, ...], defect: str) -> None:
    for peer, name in zip(peers, ToolName, strict=True):
        key = "name" if name == ToolName.SANCTIONS else "customer_id"
        arguments: dict[str, object] = {
            key: "Demo Ember Holdings" if name == ToolName.SANCTIONS else "demo-customer-002",
        }
        if defect == "missing":
            arguments = {}
        elif defect == "wrong-type":
            arguments[key] = 123
        elif defect == "blank":
            arguments[key] = "  "
        elif defect == "extra":
            arguments["unapproved"] = "value"
        else:
            arguments[key] = "Demo\x00Ember"
        result = await peer.call_tool(name, arguments)
        assert result.is_error
        assert result.structured_content is None


async def test_unknown_tool_is_protocol_error(peers: tuple[Client, ...]) -> None:
    for peer in peers:
        with pytest.raises(MCPError) as error:
            await peer.call_tool("unknown_tool", {})
        assert error.value.error.code == -32602


async def test_invalid_cursor_is_protocol_error(peers: tuple[Client, ...]) -> None:
    for peer in peers:
        with pytest.raises(MCPError) as error:
            await peer.list_tools(cursor="not-a-page")
        assert error.value.error.code == -32602


async def test_empty_results_are_not_errors(peers: tuple[Client, ...]) -> None:
    for peer, name in zip(peers, ToolName, strict=True):
        arguments = (
            {"name": "Unknown Fictional Entity"}
            if name == ToolName.SANCTIONS
            else {"customer_id": "demo-customer-999"}
        )
        result = await peer.call_tool(name, arguments)
        assert not result.is_error
        assert result.structured_content["records"] == []


async def test_concurrent_calls_do_not_mix_customer_records(peers: tuple[Client, ...]) -> None:
    results = await asyncio.gather(
        *(
            peers[0].call_tool(ToolName.TRANSACTIONS, {"customer_id": f"demo-customer-{suffix}"})
            for suffix in ("001", "002", "003")
        )
    )
    for result, suffix in zip(results, ("001", "002", "003"), strict=True):
        assert not result.is_error
        assert all(
            record["customer_id"] == f"demo-customer-{suffix}"
            for record in result.structured_content["records"]
        )
