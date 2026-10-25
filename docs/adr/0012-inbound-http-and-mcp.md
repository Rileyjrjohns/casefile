# ADR 012: Shared commands through HTTP and MCP

Status: Accepted.

FastAPI and the official MCP server expose the same commands and queries through
bootstrap composition. Feature-owned inbound documents validate inputs; neither
transport implements business transitions. The worker remains asynchronous.
StdIO offers an isolated local demo; combined Streamable HTTP shares persistent
state with the UI. Inbound MCP is an interface, separate from outbound evidence.

Review always requires elicitation. Modern protocol tokens bind exact arguments,
expire and cannot be reused; legacy sessions use form elicitation. Application
revision and access checks still run after confirmation. The demo identity is
explicitly limited; client honesty cannot replace production authentication.
