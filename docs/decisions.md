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

The default policy flags injection with a documented, normalized pattern set (a
heuristic, not a classifier) and uses fixed support scores;
it is not a learned confidence estimator. **`local` means demo policy plus a
learned risk model**: `LocalDecisionModel` answers `risk` with the trained
`RiskModel` port and delegates every other purpose to the explicit demonstration
policy. It is not a local language model. The tabular model is not an injection or
language classifier. See [ADR 024](adr/0024-two-language-model-ports-and-the-meaning-of-local.md).

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
replies and usage counters. Narrative generation has its own, differently shaped
`LanguageModel` port in the investigation feature and its own HTTP client: features
stay independent and `shared/` cannot hold third-party code, so the two small
clients are deliberately not merged ([ADR 024](adr/0024-two-language-model-ports-and-the-meaning-of-local.md)).
Both adapters use httpx, reject URL credentials/query strings, disable redirects,
cap responses at one megabyte and default to a ten-second request timeout.
Failed calls retain reservations. Prices are estimates, not reconciled invoices.

`model_route` chooses only configured narrative handles: `primary`, plus
`conservative` when `CASEFILE_FALLBACK_MODEL_*` is supplied using the narrative
[configuration suffixes](agent.md). Conservative output caps at 512 tokens;
primary at 1,024. The composed worker has a four-minute execution timeout inside
a five-minute lease. Unknown interrupted calls require a new run after recovery.

## Adapter contract

`tests/contracts/test_decision_model.py` is the single behavioural suite for the
`DecisionModel` port. It runs the same questions and assertions against the fake
policy, `local` (with an in-memory risk predictor), `jev` over a mock transport
returning schema-valid System One documents, and `llm` over the real decision HTTP
client with a mock transport. Every adapter must return a typed value inside its
options or rubric, a confidence in [0, 1], a distribution covering the options and
summing to one, a choice with maximal probability, a score equal to the
probability-weighted rubric position, and non-negative usage covered by its
reservation. Malformed provider responses (unknown or missing IDs, a different
operation, out-of-range or non-finite numbers, incomplete distributions, negative
usage, refusals, truncated replies, HTTP errors and oversized bodies) must raise.

## Comparative benchmark

`casefile-decisions` runs every case of both evaluation suites (the 30 findings-v1
cases and the 69 current-policy cases) through the agent with a recording decision
gateway that wraps the demonstration decisions, and measures adapters on the
requests the agent really sent: purpose, operation, state and options. The
questions are therefore the production ones, including the recommendation state with
signals, evidence gaps and workflow, which selects the dedicated dossier
instructions. Tabular risk is excluded and evaluated separately. Each adapter
receives the same sequence, independently of its own upstream answers. Routing
questions reflect the demo policy's earlier choices because that is how the
recording was made.

Guard positives are the states the agent sent for each case's tool calls. Four
synthetic denials per case (another customer, an unrelated sanctions name, a
disallowed tool, a repeated call, built with the agent's own state builder) are
negatives, and one unsupported conviction claim per case is a negative signal
control. Question and label logic lives in the evaluation feature
(`evaluation/domain/decision_questions.py`); bootstrap only records and maps.
Expected answers are not supplied to providers.

Each label has a declared basis, shown in `decisions.md`:

| Decision | Label | Basis |
| --- | --- | --- |
| recommendation | The case's expected recommendation | reference |
| signal | Whether the key is an expected signal; unsupported claims are negative | reference |
| injection | Whether the batch or record is cited by an expected `instruction:` signal | reference |
| priority | High for sanctions alerts (`sanctions_match`, `sanctions_identity_review`), normal otherwise | documented rule |
| tool_route | The first available tool in the documented collection order | documented rule |
| model_route | `primary` | documented rule |
| alert_type | The case's declared kind | consistency check |
| guard | Accepted for recorded calls, denied for the synthetic negatives | consistency check |

A **consistency check** shares its input with the policy under test, so agreement
shows that the adapter and the label builder have not drifted apart, not that the
decision is right. A documented rule is stated independently but a policy can
implement the same rule. Only reference labels come from the case expectations, and
those expectations were written by the authors of the rules and still await
independent human review. A choice with a single option, such as `model_route`
without a configured fallback narrative model, is resolved locally and is trivially
correct for every adapter.

Per purpose: accuracy, ten equal-width bin ECE, nearest-rank p50/p95 wall time,
estimated USD per represented case and failures. Choice ECE uses selected-option
probability, not Jev's certainty statistic. Binary and two-level scores use a
0.5 boundary. Failures count as incorrect, retain reservations and are not dropped.
Risk calibration uses separate held-out transaction labels because the golden
cases have no independently labeled risk outcomes.

### Accuracy gate

`evals/decision-thresholds.toml` sets a minimum accuracy per purpose for the offline
`local` adapter. The floors are regression guards taken from a measurement (rounded
down), not quality targets: the demo policy scores 0.889 on priority because it does
not mark `sanctions_identity_review` alerts high, and 0.582 on signal because its
fixed support scores place neutral evidence above the 0.5 boundary. `casefile-decisions`
exits non-zero and names every gated purpose that falls below its floor or is not
measured; `--thresholds` selects another file. A test runs a deliberately degraded
adapter through the gate. Raise a floor when the policy improves and never lower
one to make a regression pass. Jev and LLM rows are never gated.

### Replay fixtures

Offline `evals/decision-replay/` files are **synthetic protocol examples, not
captured vendor outputs**. They are generated by `casefile-decisions
--write-synthetic-fixtures` from the questions above, answered by the demo policy,
with provenance `synthetic-protocol-fixture`; the replay transport matches requests
exactly and has no network fallback. Because they derive from the local policy,
the local, Jev and LLM rows of an offline run are identical by construction. They
test the adapters' protocol handling and nothing about vendor performance: accuracy
and ECE test the fixtures, latency is local and cost is zero. **Real Jev recordings
require your own API key and have not been recorded**; `make eval-decisions-live`
produces them against a provider you configure. CI gates successful contracts and the
local accuracy floors; structural evals separately enforce their strict thresholds.

```sh
make eval
make eval-decisions-live
make eval-decisions-live DECISION_PROVIDERS="local jev" DECISION_MAX_COST=0.05
uv run --env-file .env --locked casefile-decisions --live --providers local jev --max-cost 0.05 --max-tokens 500000
```

The accuracy gate applies whenever `local` is selected, in live mode too.
Both modes accept `--providers` followed by a nonempty selection of `local`,
`jev` and `llm`. The default still measures all three adapters. Only selected
remote providers require credentials or offline recordings; all selected live
settings are validated before the first provider call. The complete question set
is preserved regardless of selection.

Reports record the measurement timestamp, returned model versions and the
fingerprints of both datasets. Individual observations include the suite, whether
the question was recorded or a synthetic negative, its label basis, the expected
and actual answers and the model, so disagreements can be inspected without
exposing credentials. A report's `passed` flag means all calls completed
successfully and no local accuracy floor was violated, not that every answer matched
the reference.

Total reservation limits default to USD 1 and 2,500,000 tokens. Denied reservations
make no request and remain failed observations. There are no provider retries.
Reports exclude keys and exception messages. The manual workflow uses the
`model-evaluations` environment on the default branch, supports provider selection
and uploads reports on failure too.

The [October 24, 2026 live Jev measurement](measurements/jev-2026-10-24.md)
documents the first selected-provider run, including its disagreements and
machine-readable observations. It does not replace the frozen offline fixtures.
It predates the fact-based guard and the recorded question set: its 294 questions
were built by hand (two-field recommendation states, flag-based guards) and its
rows are not comparable with current results.
