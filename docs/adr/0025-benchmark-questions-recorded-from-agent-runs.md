# ADR 025: Benchmark questions recorded from agent runs, with a local accuracy gate

Status: accepted.

## Context

`casefile-decisions` built its questions by hand in `bootstrap`. They differed
from what the agent sends: the recommendation question carried two fields while
the agent also sends the signals, the evidence gaps and the workflow, and the
Jev adapter switches to dedicated dossier instructions when `evidence_gaps` is
present, so production prompts were never measured. Only the guard questions had
been fixed to use the agent's own state builder (ADR 021). The offline Jev and
LLM recordings were generated from the local policy, so local, Jev and LLM rows
were identical, and the command failed only on execution errors, so no accuracy
was gated. Question and label construction, which is evaluation logic, lived in
`bootstrap`.

## Decision

- **Record, do not reconstruct.** `bootstrap` runs every case of both evaluation
  suites (the 30 findings-v1 cases and the 69 current-policy cases) through the
  agent with a recording decision gateway that wraps the demonstration decisions,
  and keeps each `DecisionRequest` (key, operation, purpose, state, options).
  Tabular risk requests are excluded; the risk model has its own held-out
  evaluation. The guard negatives of ADR 021 (another customer, an unrelated
  sanctions name, a disallowed tool, a repeated call) and one unsupported-claim
  control per case are added next to the recorded questions. Each adapter then
  receives the same recorded sequence, whatever its own upstream answers would
  have been.
- **Labels live in the evaluation feature.** `evaluation/domain/decision_questions.py`
  maps plain values (a recorded request and the facts a reference case declares)
  to labels, with no investigation or decisioning import. `bootstrap` only records
  and maps types. Each label declares its basis:
  - `reference`: from the case's reference analysis. Recommendation is the expected
    recommendation, signal support is whether the key is an expected signal (the
    unsupported-claim controls stay negative), and injection is whether the batch
    or record is cited by an expected `instruction:` signal.
  - `documented-rule`: a documented rule applied to declared facts. Priority is high
    for sanctions alerts (`sanctions_match` and `sanctions_identity_review`), tool
    route is the documented collection order among the available options, and model
    route is `primary`.
  - `consistency`: the policy under test reads the same field or fact, so agreement
    only shows that adapter and label builder have not drifted. This covers alert
    type (the declared kind) and the guard.
- **Gate the offline adapter.** `evals/decision-thresholds.toml` holds a minimum
  accuracy per purpose for `local`, set from a measurement and rounded down. They
  are regression floors, not quality targets: they include 0.888 for priority and
  0.582 for signal because the demo policy really scores 0.889 and 0.582 there.
  `casefile-decisions` exits non-zero and names each purpose that falls below its
  floor or is not measured. Remote providers are never gated.
- **Synthetic recordings stay synthetic.** `evals/decision-replay/*.json` are
  regenerated with `casefile-decisions --write-synthetic-fixtures` from the new
  questions, with provenance `synthetic-protocol-fixture`. They derive from the
  fake policy and therefore still tell nothing about Jev or an LLM. Real Jev
  recordings need the user's API key and have not been recorded.

## Consequences

- Questions follow the agent: a change to a decision state changes the recorded
  questions and requires regenerating the fixtures, which the exact-match replay
  transport enforces.
- A policy change that improves a purpose should raise its floor in the same
  commit; lowering a floor is a visible, reviewable change.
- The priority label exposes that the demo policy does not mark
  `sanctions_identity_review` alerts high. The policy is unchanged here and the
  gap stays visible in the report and in `docs/known-limitations.md`.
- Choices with a single option (`model_route` without a fallback narrative model,
  and the last remaining tool route) are recorded as sent but are trivially
  correct for every adapter, and the report says so.
