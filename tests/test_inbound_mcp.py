import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass

import pytest
import uvicorn
from jsonschema import Draft202012Validator
from mcp import Client, types
from mcp.client.session import ClientRequestContext
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError

from casefile.bootstrap.mcp_server import build_server
from casefile.bootstrap.runtime import create_memory_runtime

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Confirmation:
    accept: bool = False
    requests: int = 0

    async def __call__(
        self, context: ClientRequestContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult:
        self.requests += 1
        assert "human investigator" in params.message
        assert "expected_revision" in params.message
        return types.ElicitResult(
            action="accept" if self.accept else "decline", content={"confirm": self.accept}
        )


@pytest.fixture(params=["in-process", "legacy", "stdio", "http"])
async def peer(request: pytest.FixtureRequest) -> AsyncIterator[tuple[Client, Confirmation]]:
    confirmation = Confirmation()
    async with AsyncExitStack() as stack:
        if request.param == "stdio":
            client = Client(
                StdioServerParameters(
                    command=sys.executable, args=["-m", "casefile.bootstrap.mcp_server"]
                ),
                elicitation_callback=confirmation,
                read_timeout_seconds=10,
            )
        else:
            server = build_server(await create_memory_runtime())
            if request.param == "http":
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                http = uvicorn.Server(
                    uvicorn.Config(server.streamable_http_app(), log_level="error", lifespan="on")
                )
                task = asyncio.create_task(http.serve(sockets=[sock]))
                for _ in range(100):
                    if http.started:
                        break
                    await asyncio.sleep(0.01)
                assert http.started

                async def stop() -> None:
                    http.should_exit = True
                    await asyncio.wait_for(task, 5)
                    sock.close()

                stack.push_async_callback(stop)
                client = Client(
                    f"http://127.0.0.1:{port}/mcp",
                    elicitation_callback=confirmation,
                    read_timeout_seconds=10,
                )
            else:
                client = Client(
                    server,
                    elicitation_callback=confirmation,
                    mode="legacy" if request.param == "legacy" else "auto",
                )
        yield await stack.enter_async_context(client), confirmation


async def test_inbound_protocol_discovery_queries_and_human_confirmation(
    peer: tuple[Client, Confirmation],
) -> None:
    client, confirmation = peer
    tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == {
        "list_cases",
        "get_case",
        "start_investigation",
        "get_run_trace",
        "submit_review",
    }
    for tool in tools:
        Draft202012Validator.check_schema(tool.input_schema)
        assert tool.input_schema["additionalProperties"] is False
    cases = (await client.call_tool("list_cases", {})).structured_content
    assert cases is not None and len(cases["cases"]) == 3
    case_id, run_id = "00000000-0000-0000-0000-0000000003ea", "00000000-0000-0000-0000-0000000007d2"
    assert not (await client.call_tool("get_case", {"case_id": case_id})).is_error
    assert (await client.read_resource(f"case://{case_id}")).contents
    assert (await client.read_resource(f"run://{run_id}")).contents
    assert len((await client.list_resource_templates()).resource_templates) == 2
    assert len((await client.list_resources()).resources) == 3
    assert (await client.list_prompts()).prompts[0].name == "triage_case"
    assert (await client.get_prompt("triage_case", {"case_id": case_id})).messages
    assert (await client.call_tool("get_case", {"case_id": "bad"})).is_error
    with pytest.raises(MCPError):
        await client.call_tool("unknown", {})
    with pytest.raises(MCPError):
        await client.read_resource("case://not-a-uuid")
    for _ in range(100):
        detail = (await client.call_tool("get_run_trace", {"run_id": run_id})).structured_content
        assert detail is not None
        if detail["run"]["state"] == "AWAITING_REVIEW":
            break
        await asyncio.sleep(0.02)
    assert detail["run"]["state"] == "AWAITING_REVIEW"
    payload = {
        "run_id": run_id,
        "expected_revision": detail["revision"],
        "decision": "escalate",
        "confirmed": True,
        "conclusions": [
            {"conclusion_id": item["id"], "action": "validate"}
            for item in detail["run"]["analysis"]["conclusions"]
        ],
    }
    declined = await client.call_tool("submit_review", payload)
    assert declined.is_error
    assert confirmation.requests == 1, declined
    confirmation.accept = True
    assert not (await client.call_tool("submit_review", payload)).is_error
    closed = (await client.call_tool("get_run_trace", {"run_id": run_id})).structured_content
    assert closed is not None and closed["run"]["state"] == "CLOSED"


async def test_confirmation_is_bound_to_exact_arguments_and_cannot_be_replayed() -> None:
    runtime = await create_memory_runtime()
    while await runtime.worker.run_once():
        pass
    run_id = "00000000-0000-0000-0000-0000000007d2"
    server = build_server(runtime, background_worker=False)
    async with Client(server) as client:
        await client.list_tools()
        detail = (await client.call_tool("get_run_trace", {"run_id": run_id})).structured_content
        assert detail is not None
        payload = {
            "run_id": run_id,
            "expected_revision": detail["revision"],
            "decision": "escalate",
            "confirmed": True,
            "conclusions": [
                {"conclusion_id": c["id"], "action": "validate"}
                for c in detail["run"]["analysis"]["conclusions"]
            ],
        }
        inputs: types.InputResponses = {
            "human_review": types.ElicitResult(action="accept", content={"confirm": True})
        }
        assert (await client.call_tool("submit_review", payload, input_responses=inputs)).is_error
        pending = await client.session.call_tool(
            "submit_review", payload, allow_input_required=True
        )
        assert isinstance(pending, types.InputRequiredResult)
        altered = {**payload, "decision": "close"}
        assert (
            await client.call_tool(
                "submit_review",
                altered,
                input_responses=inputs,
                request_state=pending.request_state,
            )
        ).is_error
        assert (
            await client.call_tool(
                "submit_review",
                payload,
                input_responses=inputs,
                request_state=pending.request_state,
            )
        ).is_error
        next_pending = await client.session.call_tool(
            "submit_review", payload, allow_input_required=True
        )
        assert isinstance(next_pending, types.InputRequiredResult)
        invalid: types.InputResponses = {
            "human_review": types.ElicitResult(action="accept", content={"confirm": "true"})
        }
        assert (
            await client.call_tool(
                "submit_review",
                payload,
                input_responses=invalid,
                request_state=next_pending.request_state,
            )
        ).is_error
        unchanged = (await client.call_tool("get_run_trace", {"run_id": run_id})).structured_content
        assert unchanged is not None and unchanged["run"]["state"] == "AWAITING_REVIEW"
