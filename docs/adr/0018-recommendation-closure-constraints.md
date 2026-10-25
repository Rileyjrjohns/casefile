# ADR 018 — Deterministic constraints on closure recommendations

- Status: accepted
- Date: 2026-10-24

## Context

The live decision benchmark exposed recommendation disagreements on injected
notes. Reproduction through the complete API and worker gives different results:
Jev receives the extracted instruction signal, not the complete raw snapshot used
by an independent benchmark question. The three reproduced cases did not propose
closure, but the application previously accepted any valid typed model choice.
A provider change could therefore recommend closure despite an observed concern.

## Decision

Apply `constrain_closure` after the model's recommendation and before accepting
the analysis. It operates on the approved, sourced signals and collected KYC:

- Replace `close` with `escalate` when any unresolved concern remains, including
  a record-level detected instruction, KYC mismatch or documentary discrepancy.
- Preserve the existing 0.5 threshold for synthetic transaction-risk concerns.
  Low risk predictions and corroborating `context:` signals do not block closure.
- Replace `close` with `request_information` for missing KYC or evidence gaps when
  there is no established concern. A banking dossier with no usable findings also
  remains incomplete.
- Preserve `escalate` and `request_information` choices. This is a constraint on
  closure, not a replacement for every model decision.

An evidence-support score cannot remove an already extracted concern. The rule
does not invent signals, modify the provider reply, authenticate documents or
claim general injection detection. A human may resolve or reject the concerns
and explicitly choose the final outcome through the existing review process.

When an adjustment occurs, retain the original `decision.recommendation.final`
event and append `policy.recommendation.v1` with the proposed outcome, blocking
signal keys, effective recommendation and reason codes. This is a local,
unmetered transition: no additional model request or budget reservation occurs.
The UI explains the adjustment alongside the recommendation.

Retries reuse and verify the recorded policy event; they do not duplicate it or
repeat the successful model call. PostgreSQL persists the event atomically with
the run revision. Replay with this policy reproduces the adjusted trace exactly.
Historical stored investigations are never rewritten. Re-evaluating an older
unsafe recommendation under a newer policy may change the derived result; exact
historical behavior requires the original application policy version.

## Consequences

Provider prompts, the frozen reference corpus and its replay fixtures remain
unchanged. The standalone decision benchmark still measures raw provider answers
and must not report the constrained result as improved model accuracy.
Positive closure remains possible for a resolved identity homonym or routine
activity. No code path creates a human decision or closes the run automatically.

Tests force a closing recommendation and zero support scores on injected and
contradictory evidence, verify incomplete and benign cases, exercise retry and
exact replay, and check PostgreSQL persistence and the reviewer-facing notice.
