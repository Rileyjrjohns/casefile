# HTTP, MCP and review interfaces

`casefile-serve` composes API, worker and inbound MCP. Without
`CASEFILE_DATABASE_URL`, it uses memory. Compose uses PostgreSQL and migrations.
`CASEFILE_TOOLS=memory|mcp` selects evidence adapters;
`CASEFILE_LANGUAGE_MODEL=fake|http` selects the primary narrative model.

## HTTP

The default listener is `127.0.0.1:8000`; `/docs` contains schemas.
`POST /cases` accepts alert ID, subject `demo-customer-NNN`, description and kind
(`unusual_transaction`, `sanctions_match`, `kyc_inconsistency`, `payment_fraud`,
`credit_document_fraud`, `sanctions_identity_review`).
`POST /cases/{id}/runs` accepts optional run ID and budget limits, defaulting to
5 tool calls (6 for the extended banking kinds), 100,000 tokens and USD 1.
Explicitly supplied limits are never increased. `GET /cases` supports `limit` (1–100) and
nonnegative `offset`. `GET /runs/{id}` includes revision and ordered events.
Source URLs only return records observed in that run, including exact persisted
risk features, version and prediction.

`GET /cases/{id}/runs` returns investigations newest-created first. UUID values,
later checkpoints and retries do not determine their order. PostgreSQL records
an insertion sequence; memory storage preserves insertion order. Migration
`0003` backfills older runs from their first trace timestamp, falling back to
the job schedule and then UUID to break ties. That historical order is an
approximation where the original enqueue time was not retained.

Review payloads contain `expected_revision`, `decision`, `confirmed` and
`conclusions`. Each conclusion has `conclusion_id`, `action` (`validate`,
`correct`, `reject`), optional `corrected_statement` and `reason`. Every
conclusion must occur exactly once. Corrections require replacement text and a reason;
rejections require a reason. Review therefore requires an explicit action for every
conclusion plus confirmation. The HTTP API accepts `confirmed: true` from any caller
and attributes the review to the fixed demo reviewer: it has no authentication, so the
flag proves nothing about who sent it. Missing confirmation returns 422; stale revisions
or already closed runs return 409. Review and closure commit atomically.
Correction promotion stores separate curator candidates, not golden-file edits.

`POST /runs/{id}/conclusions/{conclusion_id}/reference` accepts
`{"confirmed": true}` after a committed human correction. It requires the demo
curator role, rejects unreviewed runs (409), missing confirmation and non-corrections
(422), and is idempotent for a review/conclusion pair. `GET /references/{id}`
downloads a versioned JSON candidate with the original case, run, evidence trace,
human review and expected corrected statements. The bundle is marked
`requires_curation`; it does not alter `evals/golden/`. PostgreSQL retains the
candidate across server restarts. Promotion/export is available through HTTP/UI;
the five inbound MCP tools remain the investigation and human-review interface.

All demo requests use one fixed reviewer. `CASEFILE_ACCESS_TOKEN` optionally
requires a bearer token except on `/health`. This is a local access gate, not
user authentication. The static UI does not collect tokens. Host names are
restricted to loopback and foreign-Origin writes are rejected. Nginx limits
bodies to 128 KiB, adds a restrictive CSP and proxies same-origin `/api/` requests.
The API itself refuses bodies above 1 MiB with 413 (`payload_too_large`) and answers
validation failures with 422 listing only each error's location and type, never the
submitted values; client-chosen key names longer than 64 characters are replaced by a
placeholder and at most 32 errors are listed. Direct API deployments still need equivalent proxy limits.

## MCP

```sh
uv run --locked casefile-mcp
uv run --locked casefile-mcp --http --port 8001
make mcp-inspect
```

Standalone commands use isolated in-memory cases. Standalone HTTP serves `/mcp`;
the combined server serves `/mcp/` on port 8000, sharing API state. SDK host/origin
checks protect MCP HTTP. The official inspector is pinned in the Makefile and
downloaded on first invocation.

Discovery advertises strict inputs for `list_cases`, `get_case`,
`start_investigation`, `get_run_trace`, `submit_review`. Outputs are JSON objects.
Resources are `case://{id}`, `run://{id}`; prompt `triage_case` marks evidence as
untrusted. Unknown tools/resources raise protocol errors; validation and business
failures return error tool results.

Review presents the exact actions, decision and revision through elicitation.
A supplied `confirmed` flag never substitutes for that exchange. Legacy sessions
use form elicitation; protocol 2026-07-28 uses `InputRequiredResult` with a random,
single-use, five-minute token bound to the exact arguments and generated review
ID. Changed, expired, unsolicited and replayed responses are rejected. The final
command rechecks access and revision. Pending tokens are process-local, capped
at 100, and invalidated by restart. SDK clients handle the exchange automatically.

The server requests human confirmation through elicitation and rejects a bare
`confirmed` flag, but it cannot prove that a human answered. A client whose
elicitation callback auto-accepts can submit a review (the in-process tests do
exactly that), so connect only trusted clients. Trusted client UI and an
authenticated reviewer identity remain prerequisites for real deployment.
Tests cover denial, acceptance, altered submissions, token replay and non-boolean
confirmation in modern and legacy sessions.

## Frontend

The single screen polls active runs and displays sources as escaped text.
Structured supporting documents display readable fields, source provenance and
an expandable exact JSON record. The three [banking dossiers](banking-scenarios.md)
include a scenario brief and start only on an explicit investigation request.
Changing a review clears confirmation. Revision conflicts preserve the draft and
offer reload. Sources cannot appear after switching cases or investigations.
The history selector opens an older run with its original analysis and recorded
human review. Switching runs clears unsaved actions and confirmation. The active
investigation opens by default, or the most recent completed one if none is active.
Loading, empty, failed and closed states are visible; closed analyses and human
reviews are immutable. A committed correction offers **Save regression candidate**,
then **Download candidate**, without resubmitting or changing the review.
When every run is closed or failed, **Start new investigation** queues a separate
run and selects it. Prior reviews remain unchanged, and the server still enforces
at most one active run per case. Case creation remains available via API/MCP.

**How this analysis was produced** reads model identities from successful recorded
events, rather than the server's current configuration. It separates Jev, demo
rules, local synthetic risk and the demo template. Support badges are not fraud
probabilities. Seeded labels CF–001 through CF–006 stay stable when cases are added;
new cases use their alert ID.
