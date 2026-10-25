# Evaluations

Run the reference suite from the repository root:

```sh
make eval
```

This writes `build/evals/report.md` and `build/evals/report.json` and exits with
status 0 on success, 1 when a gate fails, or 2 for invalid configuration. Reports
are written before returning a failed gate. `build/` is already excluded as build
output. No golden or replay fixture is written by the runner.

The default mode uses `ReplayEvidenceGateway` and `ReplayLanguageModel` with no
database, MCP processes, model credentials or network calls. Even if model or
OTLP variables are set, replay does not use them. Override the output directory
with `make eval EVAL_OUTPUT=/tmp/casefile-evals`.

## Reference cases and review

[`evals/CASES.md`](../evals/CASES.md) indexes all 30 cases. Each JSON file in
`evals/golden/` contains a fictional case context, exact tool response snapshots,
an expected summary, keyed signals with confidence and citations, a recommendation
and the annotation rationale. There are eight scenario categories: clear,
boundary, volume, sanctions, KYC, injection, missing data and mixed signals.

The initial annotations describe the historical `findings-v1` policy. They are
pending independent human review. Review the rationale against the evidence and
the intended behavior, especially the documented limitations in cases 009 and
015. They intentionally expose the absence of cumulative-volume detection and
the treatment of an unmatched sanctions alert. A passing suite does not approve
those limitations for a real deployment.

The runner and typed-decision benchmark explicitly retain `findings-v1` so the
30 cases and the dated vendor measurement remain comparable. Current runtime
investigations use `findings-v2`: the evidence from case 009 yields a sourced
monthly-volume concern, and case 015 yields an unresolved-screening evidence gap.
`tests/test_findings_v2.py` covers those outcomes, currency and month boundaries,
duplicate transactions, exact current replay and historical-run restart behavior.
The 30/30 historical score must not be described as a measurement of v2.

The files in `evals/replay/` contain frozen synthetic narrative requests,
responses and reservations. They are authored fixtures, not recordings from a
commercial model. The runner builds call events from the tool snapshots and
these frozen model exchanges, then passes them through the production replay
adapters. Requests must match exactly. Changes to context, evidence, approved
signals, prompt version or output limit cannot silently reuse an old response.

Expected annotations are never computed by calling the agent under evaluation.
The runner obtains predicted signal identities from the executed model request
and persisted conclusion IDs, not from expected labels. Both sets of fixture
bytes and their filenames contribute to the report's dataset SHA-256.

To change the dataset, propose a separate, justified data change for review.
Do not regenerate or edit labels or replay replies to make a failing code change
pass. Intentional policy or prompt changes need a reviewed baseline migration.
Promoted human corrections remain candidate reference examples in the database;
they do not automatically alter this suite. A curator must assemble their complete
fictional evidence and review expectations before adding a case.
The review screen exposes this flow after a committed correction. Its JSON
download contains `status: requires_curation`, the correction, original case,
analysis, journal and human review, plus expected conclusions and recommendation.
Rejected conclusions are excluded from expectations; the original analysis and
its citations are retained unchanged. See the [HTTP contract](interfaces.md).

## Metric definitions

| Metric | Definition |
| --- | --- |
| Signal precision | Micro-averaged `TP / (TP + FP)` over exact signal keys |
| Signal recall | Micro-averaged `TP / (TP + FN)` over exact signal keys |
| Recommendation accuracy | Correct recommendations from completed runs divided by all cases |
| Invalid source rate | Unobserved `(tool, record ID)` citations in accepted conclusions divided by all accepted citations |
| Completion rate | Cases reaching `AWAITING_REVIEW` without errors divided by all cases |
| Estimated cost | Exact decimal usage in USD; unresolved failures retain the run's reservation |
| Tokens | Settled token usage, or reserved tokens when actual usage is unknown |
| Latency | Wall-clock milliseconds from fixture validation through isolated local run execution |

A predicted key counts at most once as a true positive. Duplicate predictions
count as false positives. For an empty expected set and empty predictions,
precision and recall are 1. If signals were expected but none were produced,
both are 0. An empty citation denominator gives a source-invalidity rate of 0;
the mandatory completion gate prevents rejected model outputs from passing by
producing no conclusions. A failed run remains in every applicable denominator.
Source validity establishes record provenance, not factual entailment of prose.

The JSON report includes expected and predicted keys and recommendations,
accepted narrative summaries and conclusions, returned model identity, confidence,
citations, sanitized error categories, thresholds and dataset identity. The
Markdown report includes aggregate metrics and a row for every case. Latency
p50 and p95 use the nearest-rank method. Wall time varies between runs even when
all structural replay results are deterministic.

`evals/thresholds.toml` currently requires at least 30 cases, perfect signal
precision and recall, perfect recommendations, no invalid sources, at most
USD 0.05 per case and at most 30,000 ms per case. Every case must complete;
this condition cannot be disabled in the threshold file. A nonzero allowed
invalid-source rate is rejected as invalid configuration. These are fixed
acceptance thresholds, not statistical comparisons against a previous artifact.

## CI and live evaluation

The PR workflow runs `make check` and `make eval`, then uploads both reports,
including on evaluation failure. There is no live service access in PR evals.
Dependency installation and artifact upload remain normal CI infrastructure
operations. Configure the check as required in repository branch protection if
merges must be blocked; the workflow itself returns failure on any regression.

The separate **Live model evaluations** workflow is manually dispatched on the
default branch and uses the `model-evaluations` environment. Configure:

| Setting | GitHub location | Meaning |
| --- | --- | --- |
| `CASEFILE_MODEL_API_KEY` | Environment secret | Narrative provider credential |
| `CASEFILE_MODEL_URL` | Environment variable | HTTPS chat-completions-compatible endpoint |
| `CASEFILE_MODEL_NAME` | Environment variable | Requested model identifier |
| `CASEFILE_MODEL_INPUT_COST_PER_MILLION` | Environment variable | USD per million input tokens |
| `CASEFILE_MODEL_OUTPUT_COST_PER_MILLION` | Environment variable | USD per million output tokens |
| `CASEFILE_MODEL_TIMEOUT_SECONDS` | Environment variable, optional | Request timeout, default 10 seconds |

Configure environment access restrictions as appropriate for the repository.
PR jobs do not receive this secret. The manual workflow changes only the narrative
model adapter; all tools still replay fictional snapshots. It runs cases
sequentially, makes no automatic provider retries and uploads reports even when
cases fail. Each isolated investigation has limits of five tool calls, 100,000
tokens and USD 1 in reservations; the stricter report cost threshold is USD 0.05.
An invalid provider configuration fails before cases run and has no report to
upload. No real-provider run is needed to validate the default suite.

With the same explicit environment variables configured locally:

```sh
make eval EVAL_MODE=http EVAL_OUTPUT=build/evals-live
```

This command makes up to 30 live narrative requests and may incur provider fees.
See the [agent guide](agent.md) for the HTTP contract and budget limitations.
Reported live costs are configured-price estimates, not reconciled invoices.
Provider exception messages, request headers and credentials are not exported.

## Interpretation limits

This is a regression benchmark for a deliberately small demonstration policy,
not an independently labeled fraud dataset or a comparison of model vendors.
Signal selection, confidence and recommendations still come from deterministic
rules. Live mode measures whether a narrative model preserves their structured
constraints; it does not turn these metrics into model decision accuracy.
Neither text quality, confidence calibration, identity resolution nor unseen
injection variants are scored here. The current injection rule recognizes one
literal phrase. Human review remains necessary for narrative truthfulness.

Replay fixture cost is zero and its token counters are synthetic. Its latency
is local orchestration time and cannot estimate production model latency.
PostgreSQL and MCP performance are outside this in-memory benchmark. The separate [typed adapter comparison](decisions.md) adds per-purpose accuracy,
calibration, latency and cost. Its offline provider fixtures have explicit
synthetic provenance. `make eval` runs both reports; its second gate requires
all typed adapter contract calls to succeed.

See [ADR 011](adr/0011-replay-evaluation-baselines.md) for the boundary and
baseline decisions.
