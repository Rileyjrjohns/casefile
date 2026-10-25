# ADR 009 — Validate observed sources before human review

- Status: accepted
- Date: 2026-10-24

## Context

A syntactically valid model response can invent a record, cite a real but
unrelated source or omit a signal. Tool content can also contain instructions
that must not become operational authority.

## Decision

Register exact `(tool, record_id)` references only from successfully returned
evidence and commit them with the call response. Tool documents and query
matching are checked at the adapter boundary. The domain's source set belongs
to a single run and cannot be populated by a model response.

Use a deterministic demonstration policy to select signal keys, confidence
scores and recommendations until the planned `DecisionModel` integration.
The language model supplies narrative text only. Require exactly one statement
for every approved signal and exactly its approved sources. Reject invented,
duplicate, omitted and unrelated references, even if an unrelated record was
observed elsewhere in the same run. Construct conclusions through validated
domain factories, then run the aggregate's independent source check.

A valid analysis moves to `AWAITING_REVIEW`. The agent has no review capability,
and a close recommendation does not close the run. Human confirmation and the
existing atomic review flow remain mandatory.

## Alternatives considered

- Validating only JSON shape accepts invented identifiers.
- Checking merely that a record exists in a provider permits citations to data
  never seen during this run.
- Allowing the model to assign operational decisions conflicts with the planned
  separation between narrative generation and typed decisions.

## Consequences

Every structured conclusion has traceable evidence and a deterministic identity.
This does not prove the provider's truthfulness or the factual accuracy of free
text. Tool content stays untrusted; delimiters, fixed routing and human review
are boundaries, not a complete prompt-injection detector. The demonstration
phrase rule is explicitly limited and will not be presented as a measured
security model. Evaluation metrics and stronger typed decision adapters remain
later work.
