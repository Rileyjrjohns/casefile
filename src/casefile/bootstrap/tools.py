"""Compose fictional MCP providers and own their subprocess lifetimes."""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server.stdio import stdio_server

from casefile.features.investigation.adapters.outbound.tools.client import McpInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.adapters.outbound.tools.server import PROVIDERS, build_provider


@asynccontextmanager
async def connect_demo_clients(*, banking: bool = False) -> AsyncIterator[tuple[Client, ...]]:
    async with AsyncExitStack() as stack:
        clients = []
        for provider in PROVIDERS:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "casefile.bootstrap.tools",
                    provider,
                    *(["--banking"] if banking else []),
                ],
            )
            clients.append(
                await stack.enter_async_context(
                    Client(
                        parameters,
                        read_timeout_seconds=5,
                    )
                )
            )
        yield tuple(clients)


@asynccontextmanager
async def connect_demo_tools(*, banking: bool = False) -> AsyncIterator[McpInvestigationTools]:
    async with connect_demo_clients(banking=banking) as clients:
        yield await McpInvestigationTools.discover(clients)


async def serve(provider: str, *, banking: bool = False) -> None:
    server = build_provider(PROVIDERS[provider], load_seed(banking=banking))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fictional evidence MCP server over stdio.")
    parser.add_argument("provider", choices=tuple(PROVIDERS))
    parser.add_argument(
        "--banking",
        action="store_true",
        help="Include the three extended fictional banking dossiers.",
    )
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.provider, banking=arguments.banking))


if __name__ == "__main__":
    main()
