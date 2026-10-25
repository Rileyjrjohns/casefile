# Typed decisions and provider comparison

`DecisionModel` exposes `choose`, `probability`, `score` and a local `quote`.
Closed options, finite bounded values, complete distributions and metered usage
are validated into immutable answers. Scores equal probability-weighted rubric
positions. Invalid construction returns `Result`; malformed provider replies are
technical failures. Decisions never return free text.

The agent uses this port for priority, type, next tool, pre-tool guards, injection
checks, signal support, model route and recommendation. Fact extraction supplies
candidate signals from returned records. Code independently enforces query
validation, budgets, sources and human review. Every decision has a
reserved/settled journal event and can be replayed.

## Pre-tool guard

Before each tool call the agent asks a `guard` probability question. Its state
carries only facts the application computes from the case and this run's evidence;
it never carries a precomputed verdict or authorization flag:

| Fact | Meaning |
| --- | --- |
| `tool`, `query`, `subject_id` | The proposed call and the case subject |
| `query_scope` | `case_subject` when the query equals the subject id; `case_kyc_legal_name` when the sanctions tool is queried with the legal name of a KYC profile returned in this run for the subject; otherwise `unrelated` |
| `tool_allowed` | Whether this workflow may call the tool: documents only for banking kinds, sanctions only after a KYC profile was returned |
| `already_called` | Whether the tool already succeeded in this run |

The demo policy (and therefore `local`) answers 1.0 only when `tool_allowed` is
true, `already_called` is false and the scope is not `unrelated`; a missing or
malformed fact answers 0.0. Jev and LLM providers receive the same facts and an
instruction to fail closed. A value below 0.8 fails the run with a reason naming
the failing facts, and the tool is not invoked. See
[ADR 021](adr/0021-fact-based-tool-guard.md).

The default policy recognizes one injection phrase and uses fixed support scores;
it is not a learned confidence estimator. `LocalDecisionModel` delegates risk to
the trained `RiskModel` port and uses explicit policy for other tasks. The tabular
model is not an injection or language classifier.

Runtime risk always uses the local versioned model. Each transaction with usable
KYC gets a `risk_model@risk-v1` source with persisted inputs and prediction.
Risk at least 0.5 counts as a concern; lower predictions remain informational
signals. Risk probability and evidence-support scores have different meanings.
See the [model card](../models/MODEL_CARD.md).

## Provider configuration

### Local environment file

```sh
cp .env.example .env
```

Keep your credentials in `.env`, which is ignored by Git and excluded from Docker
build contexts. Fill in `CASEFILE_JEV_API_KEY`. The endpoint, `jev-latest` model
alias and published token rates are prefilled:

```dotenv
CASEFILE_JEV_INPUT_COST_PER_MILLION=0.042
CASEFILE_JEV_OUTPUT_COST_PER_MILLION=0
```

These are USD per million tokens: Jev 1.13 and 1.14 charge for input only, with
free output, according to the [official model reference](https://docs.typesafe.ai/models),
checked on October 24, 2026. For example, 10 million input tokens cost USD 0.42
at this rate. These settings estimate usage costs and enforce local budgets;
they do not set the provider's billing price. Override them for a different
provider, model or account rate. Use a pinned model for reproducible measurements.

The template starts with `CASEFILE_DECISIONS='{}'`, so supplying a key alone does
not enable remote calls. To use Jev for priority and recommendations, replace it
with:

```dotenv
CASEFILE_DECISIONS='{"priority":"jev","recommendation":"jev"}'
```

Start or restart with `docker compose up --build`, or use `make serve` for the
local Python API. Both read `.env`; already exported shell variables take
precedence. Compose passes provider settings only to the API container at runtime.
An enabled provider requires valid settings at startup. A fresh demo automatically
investigates its three seeded cases, which will make billable calls when Jev is
enabled. Existing completed investigations are preserved; start a new investigation
from the screen to exercise the new configuration.

`CASEFILE_LLM_*` configures the optional Chat Completions-compatible decision
adapter. It is unnecessary when only Jev is selected. Narrative generation uses
its separate [model configuration](agent.md). `make eval-decisions-live` also
loads `.env`. Select `DECISION_PROVIDERS="local jev"` to compare Jev with the local
policy using only a Jev key; unselected providers need no configuration.
`make check` and `make eval` do not load `.env` and use offline fixtures.

### Routing and provider settings

`CASEFILE_DECISIONS` maps purposes to `fake`, `local`, `jev` or `llm`:

```sh
export CASEFILE_DECISIONS='{"priority":"jev","injection":"jev","signal":"llm","recommendation":"jev"}'
```

Unspecified purposes use demo policy, except `risk`, which must use `local`.
Unknown configuration fails startup. Remote providers require the following
suffixes under `CASEFILE_JEV` or `CASEFILE_LLM`:

| Suffix | Meaning |
| --- | --- |
| `_URL` | Complete HTTPS endpoint; loopback HTTP is allowed |
| `_MODEL` | Requested model; pin versions for reproducibility |
| `_API_KEY` | Bearer credential |
| `_INPUT_COST_PER_MILLION` | Explicit decimal USD input-token rate |
| `_OUTPUT_COST_PER_MILLION` | Explicit decimal USD output-token rate |

Jev uses `https://api.typesafe.ai/v1/systemone`. `choose`, `probability` and
`score` map to Choice, Noul and Score. Noul has no provider confidence field:
Casefile derives `abs(2p-1)` for common certainty and retains the probability.
Singleton choices resolve locally. See official [API](https://docs.typesafe.ai/api/system-one),
[Choice](https://docs.typesafe.ai/primitives/choice),
[Score](https://docs.typesafe.ai/primitives/score) and
[confidence](https://docs.typesafe.ai/confidence) documentation.

`LlmDecisionModel` uses a feature-owned `LanguageModel` port. Its HTTP adapter
requires Chat Completions-compatible JSON Schema output, completed non-refused
replies and usage counters. Narrative generation has its own investigation port.
Both adapters use httpx, reject URL credentials/query strings, disable redirects,
cap responses at one megabyte and default to a ten-second request timeout.
Failed calls retain reservations. Prices are estimates, not reconciled invoices.

`model_route` chooses only configured narrative handles: `primary`, plus
`conservative` when `CASEFILE_FALLBACK_MODEL_*` is supplied using the narrative
[configuration suffixes](agent.md). Conservative output caps at 512 tokens;
primary at 1,024. The composed worker has a four-minute execution timeout inside
a five-minute lease. Unknown interrupted calls require a new run after recovery.

## Comparative benchmark

`casefile-decisions` extracts identical questions from the unchanged 30 cases.
Each adapter receives the same sequence, independently of its upstream routing
answers. Guard questions are built with the agent's own state builder: the
positives are the states the agent sends for each case's recorded tool calls, and
four synthetic denials per case (another customer, an unrelated sanctions name, a
disallowed tool, a repeated call) are negatives. The local policy shares the
facts' definitions with the labels, so its guard accuracy is a consistency check,
not independent evidence. Operational labels encode demo policy; injection and recommendation
labels come from frozen analyses. Signal probes use observed candidate facts and
include an unsupported conviction claim for every case. Expected answers are
not supplied to providers. Shared policy assumptions and synthetic correlations
limit independence; labels still await independent human review.

Per purpose: accuracy, ten equal-width bin ECE, nearest-rank p50/p95 wall time,
estimated USD per represented case and failures. Choice ECE uses selected-option
probability, not Jev's certainty statistic. Binary and two-level scores use a
0.5 boundary. Failures count as incorrect, retain reservations and are not dropped.
Risk calibration uses separate held-out transaction labels because the golden
cases have no independently labeled risk outcomes.

Offline `evals/decision-replay/` files are **synthetic protocol examples, not
captured vendor outputs**. Real HTTP adapters parse them through exact request
matching with no network fallback. Their accuracy/ECE test the fixtures; latency
is local and cost is zero. They provide no vendor-performance evidence. CI gates
successful contracts; structural evals separately enforce their strict thresholds.

```sh
make eval
make eval-decisions-live
make eval-decisions-live DECISION_PROVIDERS="local jev" DECISION_MAX_COST=0.05
uv run --env-file .env --locked casefile-decisions --live --providers local jev --max-cost 0.05 --max-tokens 500000
```

Both modes accept `--providers` followed by a nonempty selection of `local`,
`jev` and `llm`. The default still measures all three adapters. Only selected
remote providers require credentials or offline recordings; all selected live
settings are validated before the first provider call. The complete question set
is preserved regardless of selection.

Reports record the measurement timestamp and returned model versions. Individual
observations include the expected and actual answers and model, so disagreements
can be inspected without exposing credentials. A report's `passed` flag means
all calls completed successfully, not that every answer matched the reference.

Total reservation limits default to USD 1 and 2,500,000 tokens. Denied reservations
make no request and remain failed observations. There are no provider retries.
Reports exclude keys and exception messages. The manual workflow uses the
`model-evaluations` environment on the default branch, supports provider selection
and uploads reports on failure too.

The [October 24, 2026 live Jev measurement](measurements/jev-2026-10-24.md)
documents the first selected-provider run, including its disagreements and
machine-readable observations. It does not replace the frozen offline fixtures.
It predates the fact-based guard: its guard rows used the earlier flag-based
questions and are not comparable with current results.
