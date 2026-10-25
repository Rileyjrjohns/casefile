# ADR 008 — Checkpointed execution and deterministic replay

- Status: accepted
- Date: 2026-10-24

## Context

Jobs have at-least-once delivery. A worker may fail between an external call and
its next save, and a replacement worker must not lose evidence, overwrite a newer
run or silently spend the same budget again. Evaluations need recorded inputs
and outputs independent of live models and tools.

## Decision

Add an `ExecutionJournal` port that commits the run and a numbered event together.
The PostgreSQL adapter stores append-only JSONB documents in `run_events` and
fences writes using the run revision and current job lease. The memory adapter
enforces the same contract within its single event loop. Trace insertion failure
must undo the run update. Migration `0002` adds the table without changing old
migrations.

Persist normalized case context, call inputs, completed responses, reservations,
reported usage and failure categories. Record `started` before invoking a tool
or model. A completed response and its effects on usage and observed sources
commit together. No database transaction spans an external call.

Reuse completed calls on retry. A known technical failure retains its reservation
and may be retried only after another reservation. An unpaired `started` event
means the outcome is unknown: a replacement worker fails the run instead of
guessing whether repeating the call is safe. The user can initiate a new run.
Terminal and awaiting-review runs are not executed again.

Use exact normalized request matching for model and evidence replay. Replay
adapters retain attempt order, reproduce known failures and interruptions, and
never fall back to a live dependency. Their data uses explicit versions and
canonical JSON. Successful responses include the served model identity; secrets
and raw vendor envelopes are excluded. Preserve run IDs when comparing complete
aggregates because conclusion IDs derive from them.

OpenTelemetry spans and structured logs are operational views. They contain
identifiers, states and failure categories, not full evidence or prompts. The
transactional journal is the authoritative replay input, independent of exporter
availability. Remote OTLP export is opt-in.

## Alternatives considered

- Saving a response separately from run usage can lose or double-charge work
  after a failure. Commit both atomically.
- Holding a database transaction across an external call prolongs locks and
  still cannot make that call transactional.
- Repeating every incomplete call may duplicate model charges without knowing
  the first outcome. Fail conservatively when the outcome was never recorded.
- Using only observability spans loses reliable replay when sampling or export
  drops a span.

## Consequences

Known failures can be replayed without understating usage. Timing, telemetry IDs
and real-clock timestamps remain nondeterministic, while normalized responses,
business state and usage can be reproduced. Unknown outcomes cannot be recovered
from missing data and stay explicit. Provider request-level idempotency and
reconciliation could later make some interrupted calls safely resumable.

Tests cover memory/PostgreSQL contracts, rollback, lease ownership, durable
reconnection, source and budget failures, MCP subprocess execution and offline
replay including a failed attempt followed by recovery. The generic HTTP model
adapter is tested using a simulated transport, not a live provider.
