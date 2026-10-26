# ADR 028: Provider recordings for offline replay

Status: accepted.

## Context

The offline `evals/decision-replay/*.json` files are synthetic protocol examples
generated from the demonstration policy ([ADR 025](./0025-benchmark-questions-recorded-from-agent-runs.md)),
so the offline Jev and LLM rows measure the fixtures, not the providers. The
`Recording` format already allowed the provenance `provider-recording`, but nothing
produced one. A live run kept no raw responses, and the replay always sent a
synthetic model name, so a recording made with the model `jev-latest` could never
have matched: replay keys are the canonical request bodies, and the model name is
part of the body.

## Decision

- **Capture at the transport.** `casefile-decisions --live --record` wraps the live
  httpx client's transport in a `RecordingTransport`. Every successful (2xx)
  response whose body is a JSON object is stored under the canonical form of the
  request body, per provider. The adapters and the budget logic are unchanged, and
  the capture sees exactly what the adapters sent and received. The first answer to
  a repeated question is kept so that a replay is deterministic.
- **Store bodies and nothing else.** A recording holds request bodies (as keys) and
  response JSON bodies. It never holds headers, the API key, the URL or a query
  string. A response that contains the key is refused as a defence in depth.
- **Schema version 2 names the request model and the instant.** `Recording` gains
  `model` (the name sent in every request) and `recorded_at` (timezone aware). A
  `provider-recording` is version 2 and requires both; a synthetic fixture stays
  version 1 with neither, so the existing files load and regenerate byte for byte.
  Unknown fields are still rejected.
- **Replay sends the recorded model.** Offline settings take the model from the
  recording, falling back to the synthetic placeholder for version 1 files, so the
  replayed bodies equal the recorded keys.
- **All or nothing per provider.** A provider gets a file only when every one of its
  questions completed. A failed call, a malformed response or a budget stop leaves
  the existing file untouched, reports the reason, and makes the run exit non-zero.
  The file is staged and renamed over the old one, so an interrupted write leaves
  the previous file whole and a link is replaced rather than written through.
- **Record within the offline budget.** Replay charges the recorded token counts
  against the default budget, so recording under a larger token budget is refused.
- **Clock and network are injected.** The recording instant is a parameter, and the
  network path is a replaceable function, so tests use `httpx.MockTransport` as the
  provider and never touch the network.

## Consequences

- CI replays genuine Jev answers offline, without a key, once a recording is
  committed. The report marks those rows `provider-recording` with the model and
  date, and still states that latency is local replay time and cost is the zero
  offline rate.
- A recording goes stale when a decision state, a prompt instruction or the question
  set changes: the request bodies then differ and the exact-match replay fails until
  a new recording is made, which bills the user's key again.
- A recording is Jev's behaviour on one day. A model alias such as `jev-latest` can
  later resolve to another version, and the response `model` field records which one
  answered. Pin a version for reproducible recordings.
- No real recording is committed by this change. `make record-decisions-jev` makes
  one with the user's own key.
