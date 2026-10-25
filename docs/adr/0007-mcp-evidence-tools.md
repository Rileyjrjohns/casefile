# ADR 007 — Outbound evidence tools over MCP

- Status: accepted
- Date: 2026-10-24

## Context

Investigations need evidence from five independent fictional systems:
transactions, KYC profiles, alert history, sanctions and investigator notes.
The application must remain testable without transports, while integration
tests must exercise actual discovery, serialization and process boundaries.
Records need stable identifiers so later analyses can cite exact sources.

## Decision

Declare one read-only, async application port per tool and immutable dataclass
responses. `InvalidToolQuery` is a business error returned through `Result`;
transport and provider failures remain exceptions. Empty successful collections
mean no matching records, never a suppressed failure. The composite
`InvestigationTools` protocol is a convenience for composition, not a requirement
for use cases to depend on every tool.

Implement these ports with an in-memory adapter and an MCP client adapter.
Use the [official Python SDK](https://github.com/modelcontextprotocol/python-sdk)
on its 2.x line, locked by uv. Each fictional provider is a separate stdio
process exposing exactly one tool. The low-level SDK server lets the provider
publish explicit schemas and distinguish protocol errors from tool errors.
The bootstrap module owns process startup, connection lifetimes and wiring.

The provider implementations live beside the outbound integration in
`investigation/adapters/outbound/tools`: they simulate external dependencies.
They do not represent Casefile's inbound API and do not call case or review use
cases. The future inbound `casefile-mcp` server will live behind the inbound
boundary, call application use cases and support stdio and Streamable HTTP.
This stage adds no inbound transport or HTTP listener.

Discover tools with `tools/list`, including pagination, and keep a private
catalog snapshot per connection. Require the five approved names and both
schemas; reject unknown or duplicate names. Limit discovery pages and timeouts.
Only local JSON Schema references are accepted, so schema validation cannot
fetch external resources. Validate inputs and structured outputs against the
discovered schemas, then validate outputs against local Pydantic documents.
The SDK's [structured tool result contract](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#structured-content)
provides the wire shape; application ports expose ordinary Python values.

Every response includes `schema_version`, its exact `tool` name and records
with stable `record_id` values. Decimal amounts travel as strings and become
`Decimal` at the boundary. Timestamps include a time zone. The client checks
tool identity, uniqueness, customer ownership, KYC cardinality and sanctions
query matching. These checks establish structural consistency; they do not
prove that an external provider is truthful.

Bundle one versioned, validated JSON seed in the package. All names, alerts,
sanctions and notes are fictional. Queries preserve dataset order and have no
time or random dependency. Sanctions use normalized exact-name or alias matching,
not fuzzy screening. A quoted injection attempt remains unmodified evidence
for later agent safeguards. Nothing in a tool response authorizes a decision.

## Alternatives considered

- Direct function calls alone would miss serialization, discovery and process
  failures. Keep them for the fake and run the same contracts over real stdio.
- A single server could expose all five tools, but would hide their independent
  connection and availability boundaries.
- Plain text output would require ambiguous parsing and make exact amounts and
  source validation harder. Require structured, versioned documents instead.
- HTTP providers would add listeners and deployment concerns before the local
  demo needs them. Stdio provides real isolation without network infrastructure.
- Sharing SDK or Pydantic models with the application would couple use cases
  to transport details. Keep those dependencies in adapters and bootstrap.

## Consequences

The five ports have shared behavioral contracts for memory and MCP. SDK protocol
tests run both in-process and against subprocesses, covering schemas, errors,
structured results and concurrent calls. Scripted peers exercise malformed
metadata, invalid output, transport failure and timeout paths. Tests require
no external business service or model key.

The seed and complete results are bounded to 100 records per collection. Larger
datasets will need explicit data pagination; the implementation never silently
truncates evidence. Catalog updates require reconnecting. Calls are not retried
automatically, leaving future orchestration responsible for budget-aware retries.

Schema validation does not detect prompt injection or establish provenance
within a run. The agent stage must register only records actually returned,
validate analysis sources against that set, account for tool calls, preserve
traces and enforce human review. Remote authentication and the inbound server
remain separate later work.
