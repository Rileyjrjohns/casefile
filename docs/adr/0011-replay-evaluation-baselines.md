# ADR 011 — Frozen reference cases and replay evaluation gates

- Status: accepted
- Date: 2026-10-24

## Context

The agent has typed tools, exact model replay, source validation and usage
accounting. Regressions must be measurable without external credentials or
changing provider behavior. The evaluation feature must remain independent of
investigation internals, and rejected runs must not disappear from reports.

## Decision

Store fictional evidence and explicit expected analyses in `evals/golden/`.
Keep synthetic, frozen model exchanges separately in `evals/replay/`. There is
no automatic recording or label regeneration during evaluations. Review fixture
changes independently from code fixes, with rationale and a new dataset hash.

Define immutable evaluation values and pure count-based metrics in the
evaluation domain. An application runner port returns observations. Only
`bootstrap` connects that port to a fresh in-memory investigation, the real
orchestration and the existing replay adapters. Models and tools do not import
the evaluation feature, and the evaluation feature does not import investigation.

Gate micro precision and recall, recommendation accuracy, accepted-source
validity, per-case cost and per-case elapsed time using a strict TOML document.
Always require all cases to reach human review. Preserve errors and usage for
failed cases, write both report formats and return a nonzero exit status. Exact
request replay rejects plan and prompt drift rather than providing a fallback.

PR evaluations use replay for every external dependency. A separate manual
workflow on the default branch uses an explicitly configured HTTP narrative
model and still replays tools. Credentials are scoped to its execution step.
Reports include dataset identity, thresholds, returned model identity and accepted
narrative output. Provider errors expose their category only. CI uploads reports
on pass or failure using a commit-pinned
[artifact action](https://github.com/actions/upload-artifact/releases/tag/v7.0.2).

## Alternatives considered

- Computing expected answers with the current policy would hide regressions.
- Calling a remote model on PRs would require credentials and introduce provider
  availability, cost and output variation into ordinary code validation.
- Ignoring failed cases would inflate accuracy and make zero invalid sources
  meaningless when every response is rejected.
- Treating replay wall time or synthetic usage as provider measurements would
  produce misleading cost and performance claims.

## Consequences

The initial 30 cases specify the current demo rules and await independent human
review. They include negative, boundary, missing-data, multi-signal and injection
examples, but they are not a realistic fraud distribution. A perfect replay score
only establishes regression consistency. Narrative truthfulness and confidence
calibration are not measured. Intentional rule or prompt changes require reviewed
baseline updates, not fixture refreshes hidden in a code fix.

Reports distinguish synthetic replay usage from live estimates and local replay
latency from live narrative latency. Generous timing bounds detect stalls without
claiming a production latency target. Comparative DecisionModel evaluation
remains a separate stage.
