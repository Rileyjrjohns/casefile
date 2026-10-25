# Fictional evidence tools

Six standalone MCP providers expose deterministic, read-only evidence. Each
provider runs in its own stdio process using the official Python SDK. They use
the bundled `seed.json`, optionally extended with `banking_seed.json`: no database,
network service, API key or real customer data is needed.

These are the simulated external systems queried by an investigation. The
inbound `casefile-mcp` server separately exposes cases, runs and human reviews.

## Providers and ports

| Provider | MCP tool / exact source name | Input | Application port |
| --- | --- | --- | --- |
| `transactions` | `list_transactions` | `customer_id` | `TransactionsTool` |
| `kyc` | `get_kyc_profile` | `customer_id` | `KycTool` |
| `alerts` | `list_alerts` | `customer_id` | `AlertsTool` |
| `sanctions` | `search_sanctions` | `name` | `SanctionsTool` |
| `notes` | `list_investigator_notes` | `customer_id` | `NotesTool` |
| `documents` | `list_case_documents` | `customer_id` | `DocumentsTool` |

Customer identifiers have the form `demo-customer-NNN`. The bundled customers
are `demo-customer-001`, `demo-customer-002` and `demo-customer-003`.
The `--banking` option adds customers 101–103 and their structured supporting
documents. The combined API server enables this option by default; see
[banking scenarios](banking-scenarios.md).
Unknown but valid identifiers return an empty collection, including KYC.
Invalid queries return `Err[InvalidToolQuery]` through application ports.
Raw MCP calls with invalid arguments return a tool error; an unknown tool or
an invalid discovery cursor produces a protocol error.

Sanctions searches match the complete name or an alias after trimming and
collapsing whitespace and applying case folding. They do not perform fuzzy or
substring matching. A match is evidence for review, not a compliance decision.
The `FICTIONAL-DEMO-LIST` is invented and has no connection to a real sanctions
list. `XZ` is an invented country code used in the fixture.

## Running a provider

Install the locked environment, then start the desired provider:

```sh
uv sync --locked
uv run --locked casefile-demo-mcp transactions
```

Replace `transactions` with `kyc`, `alerts`, `sanctions`, `notes` or `documents` to run any
of the other servers. Equivalent commands are:

```sh
make demo-mcp PROVIDER=notes
uv run --locked python -m casefile.bootstrap.tools sanctions
```

The process waits for MCP messages on standard input. Standard output is
reserved for protocol traffic; it is not an interactive command prompt.
A local MCP host can launch `uv` with arguments
`["run", "--locked", "casefile-demo-mcp", "transactions"]` and the repository
as its working directory. Each provider needs its own process configuration.

The composition helper starts all six providers with the current Python
interpreter, discovers their tools and closes every connection on exit:

```python
import asyncio

from casefile.bootstrap.tools import connect_demo_tools
from casefile.shared.result import Ok


async def main() -> None:
    async with connect_demo_tools() as tools:
        result = await tools.list_transactions("demo-customer-002")
        if isinstance(result, Ok):
            for record in result.value:
                print(record.record_id, record.amount, record.currency)


asyncio.run(main())
```

An application can inject any of the six individual ports. The composite
`InvestigationTools` protocol is only a wiring convenience. Tests can instead
use `MemoryInvestigationTools(load_seed())` without starting processes.

## Evidence and validation

`tools/list` advertises an input and output JSON Schema for each tool. The
client follows discovery pagination, requires all six known tool names and
rejects duplicate names, missing output schemas, invalid schemas and external
schema references. Discovery is bounded to 20 pages per connection, with
repeated cursors rejected. The catalog is a private snapshot for the connection;
reconnect to discover a changed catalog.

Each call validates arguments against the discovered schema. A successful
response must contain structured content, match its advertised output schema
and satisfy the local Pydantic document contract. The local contract rejects
unknown fields, unsupported versions, malformed amounts and timestamps without
a time zone. The adapter also rejects duplicate record IDs, another customer's
records, multiple KYC profiles and sanctions results unrelated to the query.
Transport failures, timeouts, tool errors and invalid responses remain technical
failures; none is converted into an empty evidence collection.

For example, `list_transactions` returns this structured document:

```json
{
  "schema_version": 1,
  "tool": "list_transactions",
  "records": [
    {
      "record_id": "txn-002",
      "customer_id": "demo-customer-002",
      "booked_at": "2026-01-11T10:00:00Z",
      "amount": "27500.75",
      "currency": "EUR",
      "counterparty": "Demo Ember Holdings",
      "country": "XZ"
    }
  ]
}
```

The response also includes the same JSON as a text content block. Application
ports return immutable dataclasses and tuples, converting decimal strings to
`Decimal`. The exact source identifier for this record is
`(list_transactions, txn-002)`. Record order follows the fixed seed order;
timestamps and IDs do not depend on the clock, random state or process lifetime.

The seed contains three customer scenarios, four transactions, three KYC
profiles, four historical alerts, two fictional sanctions entries and three
investigator notes. Loading it checks uniqueness and customer references.
One note contains a quoted prompt-injection attempt, preserved verbatim as
untrusted evidence. Tool schemas validate structure, not the truth or safety
of narrative content. The [agent](agent.md) now registers returned sources,
records call traces and enforces run budgets. The demo injection check is a
heuristic pattern set; general injection detection remains fallible.

## Tests and current limits

```sh
make test-mcp
make check
```

`make test-mcp` runs identical evidence contracts against memory and stdio MCP
adapters, protocol checks against both in-process and subprocess servers,
malformed-provider tests and seed validation. The protocol tests cover schemas,
structured content, errors, empty results and concurrent customer queries.
The full `make check` also runs PostgreSQL integration tests and requires Docker.

The demo dataset is deliberately small: each collection and response is bounded
to 100 records, with no silent truncation. There is no data pagination, fuzzy
screening, live banking integration or automatic retry. The client uses a
five-second timeout for each discovery page and tool call. Providers are
read-only and stdio-only at this stage; they do not offer network authentication
or remote deployment. The agent persists normalized call traces; provider
catalog changes still require reconnection.

See [ADR 007](adr/0007-mcp-evidence-tools.md) for the boundary and transport choices.
