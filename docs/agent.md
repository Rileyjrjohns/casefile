# Investigation agent

`InvestigationAgent` implements the worker's `RunExecutor` port. It gathers
fictional evidence, calls typed decision ports, asks a language model
for narrative text, validates the result and stops at `AWAITING_REVIEW`. It never
creates a human decision or closes a run.

## Run the demo

```sh
uv sync --locked
make demo-agent
uv run --locked casefile-investigate --mcp --customer demo-customer-002 > /tmp/casefile-run.json
```

The default uses in-memory tools and a deterministic model. `--mcp` starts all
six actual stdio providers; the historical CLI scenario makes five evidence calls.
Both modes require no API key or database. The
command creates an isolated in-memory case and run, emits the final run and
normalized call trace as JSON on stdout, and writes structured progress logs
to stderr. Redirect stdout to retain the trace after the process exits.

The demo permits five tool calls, 100,000 tokens and an estimated cost of one
configured currency unit. These are demonstration limits, not provider prices.
Production composition uses the limits supplied to `StartRun` and the durable
PostgreSQL journal. Apply migration `0002` with `make migrate` before using that
journal against a development database.

## Execution and decisions

The collection order is KYC, transactions, alert history and investigator notes,
followed by an exact sanctions search for the legal name returned by KYC. Missing
KYC leaves no name to search and yields a request-for-information recommendation
when there are no other signals. A valid unknown customer returns empty evidence.
An invalid customer identifier fails the run with a recorded query error.

The default evidence policy identifies transfers above declared monthly
volume, exact fictional sanctions matches, open KYC mismatch alerts and notes
and documents whose text matches the instruction-like pattern set. Its fixed scores and routing are
test fixtures, not trained or calibrated risk estimates. Purpose-specific providers and the versioned local risk signal are described in
[typed decisions](decisions.md). The API runtime enables risk; the isolated CLI
disables the additional synthetic risk signal.

New investigations record `policy.findings` with `findings-v2` before gathering
evidence. EUR transfers with distinct record IDs are grouped by UTC calendar month.
If several individually smaller transfers exceed the fictional KYC monthly volume
together, a concern cites the profile and every included transfer. Existing
individual-volume concerns are not duplicated by a monthly finding. The KYC wire
schema has no currency: v2 explicitly assumes an EUR baseline and creates a gap
for other currencies, excluding those amounts from comparisons and synthetic risk.
An open `sanctions_match` alert with no returned list entry creates an unresolved
screening gap; an empty response is not identity clearance. The extended identity
dossier uses its own document-based disambiguation rules. See
[ADR 019](adr/0019-versioned-evidence-findings.md).

The language model writes the narrative summary. It also returns one statement
per approved signal, which is validated structurally and kept in the journal, but
the conclusion shown to the reviewer always carries the approved signal's own
deterministic statement. The model receives the approved signal keys and sources
and cannot supply confidence scores, recommendations, tool choices or a human
decision. Its statements must cover the approved signals exactly once. Every
source must have been returned by a tool or the versioned local risk model in this
run, and belong to its corresponding approved signal. Unknown, duplicate, omitted
or unrelated sources fail validation. Conclusion identifiers are deterministic
from the run ID and signal key.

A deterministic backstop then checks the summary against the effective
recommendation and the signals. A summary that recommends closing when the
recommendation is `escalate` or `request_information`, recommends escalation when
it is `close`, or claims no concern, no risk or compliance clearance while a
concern signal exists, fails the run with `Model summary contradicts the
analysis` and the contradiction named. Negated wording such as "not a finding of
fraud" is accepted. See
[ADR 022](adr/0022-deterministic-conclusion-text-and-summary-backstop.md).

This validates structured citations and preserves human control; it does not
prove that prose is factually correct or that a provider is truthful. The
backstop is a phrase-level heuristic, not semantic entailment, so a reworded
contradiction can pass. Tool and case text remain untrusted. The pattern-based
demonstration signal is a heuristic, not a general prompt-injection detector, and
detection by a real model is fallible as well.

The application independently constrains a model recommendation of `close`.
Unresolved sourced concerns require an escalation recommendation; missing KYC or
evidence gaps require information when there is no established concern. A low
model support score cannot erase a detected instruction or documentary mismatch.
Corroborating context and synthetic risk below 0.5 do not block closure. Other
model choices remain unchanged, and the investigator retains the final decision.
The raw model answer stays in the journal; an adjustment adds a local
`policy.recommendation.v1` event and a visible explanation in the review screen.
See [ADR 018](adr/0018-recommendation-closure-constraints.md).

## Models and configuration

The `LanguageModel` port has `quote` and `generate` operations. `quote` is local
and returns a budget reservation before any external request. Three adapters
implement the port:

| Adapter | Behavior |
| --- | --- |
| `FakeLanguageModel` | Deterministic narrative and usage, with no network access |
| `ReplayLanguageModel` | Recorded responses and failures matched to exact normalized requests |
| `HttpLanguageModel` | A configurable Chat Completions-compatible HTTP endpoint |

The HTTP adapter uses structured JSON Schema output, a bounded completion and
reported token usage. Its wire shape follows the
[Chat Completions API](https://developers.openai.com/api/reference/resources/chat).
Compatibility requires the endpoint to support these fields; it is not a promise
that every model or gateway accepts them. The adapter rejects malformed output,
refusals, truncated completions, inconsistent usage and bodies larger than one
megabyte. It does not follow redirects or retry requests itself.

HTTP mode is enabled only with `--http`. Configure these variables explicitly:

| Variable | Meaning |
| --- | --- |
| `CASEFILE_MODEL_URL` | Complete endpoint URL, including the completions path |
| `CASEFILE_MODEL_NAME` | Requested model identifier |
| `CASEFILE_MODEL_API_KEY` | Optional bearer credential, required when the endpoint requires one |
| `CASEFILE_MODEL_INPUT_COST_PER_MILLION` | Nonnegative decimal input-token price |
| `CASEFILE_MODEL_OUTPUT_COST_PER_MILLION` | Nonnegative decimal output-token price |
| `CASEFILE_MODEL_TIMEOUT_SECONDS` | Timeout in `(0, 20]`, default 10 seconds |

URLs require HTTPS, except loopback HTTP for local model servers. Credentials
and query strings are not accepted in the URL. Both rates must be supplied in
the same currency as the run budget; zero is allowed for a free local endpoint.
No prices or provider defaults are guessed. The demo HTTP client ignores proxy
environment variables. Tests exercise this adapter through an HTTPX simulated
transport; the suite never makes a paid model request.

## Persistence, retries and replay

`ExecutionJournal.checkpoint` atomically saves the run and appends a numbered
event. PostgreSQL checkpoints lock the run and job, verify the revision and
active lease token against the database clock, then commit both writes.
A stale worker cannot append events or spend another reservation. A failed trace
insert rolls back the run change, including its budget update.

A call records `started` before invocation and `succeeded` or `failed` afterward.
Events retain normalized inputs, successful outputs, usage, reservations,
timestamps and failure categories. Context and state transitions are recorded
alongside calls. The journal contains full normalized evidence and model data;
HTTP headers, bearer credentials and raw vendor response envelopes are excluded.
The actual served model identifier and prompt version are retained.

On a known technical failure, the worker reschedules the job and propagates the
exception. A retry reuses completed tool/model responses from the journal, rather
than repeating them. The failed attempt retains its reservation because its
actual external cost may be unknown. A retry requires a new reservation. If a
process dies after `started` but before recording an outcome, the next owner
fails the run conservatively and requires a new human-initiated run. This avoids
silently repeating a potentially charged request with an unknown outcome.

For offline replay, inject `ReplayEvidenceGateway(events, codec)` and
`ReplayLanguageModel(events, codec)` and `ReplayDecisionGateway(events)` into a
fresh `InvestigationAgent`, preserving the risk-enabled flag and model handles. Preserve
the application policy version as well (`findings_policy="findings-v1"` for the
frozen corpus, `findings-v2` for the current-policy evaluation suite). A resumed investigation reuses its recorded findings version;
already-started legacy journals without a version use v1. Running historical evidence under a newer
closure constraint is a policy re-evaluation rather than exact historical replay. Preserve
the original case context, run identifiers and budget limits to compare the
complete final aggregate. `AgentJsonCodec` checks canonical documents and request
versions. Replay includes failed attempts and their retained reservations;
missing, changed or exhausted requests fail instead of falling back to a live
provider. Recorded interruptions raise cancellation, after which the ordinary
restart path fails the run as it did originally. Use fresh replay adapters for
each replay, since they track attempt positions.

Replay reproduces the recorded business output and usage. Execution timing,
OpenTelemetry identifiers and timestamps from a real clock are not reproduced.
An unknown external outcome stays unknown. The [evaluation guide](evaluations.md) describes the immutable reference suite.

## Budgets and observability

`Run.reserve` checks the next call before it happens. A denied reservation fails
the run without charging work that was never invoked. Successful calls replace
their reservation with actual reported usage through `Run.settle`. An overrun
records that usage, fails the run and blocks further calls. Decimal arithmetic
preserves small cost differences. The HTTP reservation counts request bytes,
framing allowance and the maximum completion, priced at the higher configured
token rate. This is conservative for the intended compatible endpoints, not a
provider-enforced billing guarantee; an unexpected reported overrun cannot undo
an external charge.

OpenTelemetry creates a run span and a child span for every invoked tool and
model or typed decision call. Structured checkpoint logs carry run ID, operation, sequence, phase
and state. Prompts, evidence, response text and credentials are excluded from
telemetry. Failure spans record the exception category without its message.
The durable journal, rather than telemetry delivery, is the replay source.

`configured_telemetry` creates a local SDK provider. Remote export is disabled
unless `CASEFILE_OTLP_TRACES_ENDPOINT` is set to a complete OTLP HTTP traces URL.
When configured, spans are exported in batches and the provider is shut down
when the application scope exits. No global provider is installed by the library.

See [ADR 008](adr/0008-checkpointed-execution-and-replay.md),
[ADR 009](adr/0009-observed-source-validation.md) and
[ADR 010](adr/0010-budget-reservations.md).
