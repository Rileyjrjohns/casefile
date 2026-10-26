# ADR 021: Pre-tool guard decided from verifiable facts

Status: accepted.

## Context

The agent asked the `guard` decision before every tool call, but the state it
sent contained `"authorized": true` and the demo policy answered by reading that
flag back. The guard was fed its own answer and could never deny. Real query
authorization lived only in `customer_query` and `sanction_query`, and the
decision benchmark reported a perfect guard score on questions the agent never
sent. Jev and LLM providers were instructed to return false "if authorization is
absent or false", which again only reads a flag.

## Decision

The investigation application layer builds the guard state with a pure function,
`guard_state` in `application/guard_policy.py`, from facts it can verify. The state
never contains a verdict or authorization flag:

- `tool`, `query` and `subject_id` (the case subject);
- `query_scope`: `case_subject` when the query equals the subject id;
  `case_kyc_legal_name` when the tool is the sanctions search and the query equals
  the legal name of a KYC profile returned in this run for the subject; otherwise
  `unrelated`. A profile of another customer never grants scope;
- `tool_allowed`: core evidence tools always; case documents only for banking
  kinds; sanctions only once a KYC profile was returned;
- `already_called`: whether the tool already succeeded in this run.

The policy is fail-closed. The demo and local policy answer 1.0 only when
`tool_allowed` is true, `already_called` is false and the scope is not
`unrelated`; any missing or malformed fact answers 0.0. Jev and LLM providers get
the same facts and an instruction to return false when a fact is missing or
unfavourable, and to ignore any permission claimed in untrusted text. A value
below 0.8 fails the run and the reason names each failing fact, for example
`unrelated query scope`. The tool is never invoked after a denial. Query
validation remains an independent check before the guard.

The decision benchmark builds guard questions with the same function. Positives
are the states the agent sends for each golden case; negatives are synthetic
realistic denials (another customer, an unrelated sanctions name, a disallowed
tool, a repeated call). Synthetic protocol fixtures are regenerated with the
project's generator and keep their provenance.

## Consequences

- The guard can deny in a real run, and the denial is traceable to a fact.
- The same facts are journaled and replayed like any other decision.
- In the demo, tool routing already prevents repeats and disallowed tools, so
  those facts mostly guard against a misbehaving router or tool adapter. The scope
  fact is the one a compromised evidence provider can trip.
- The local policy shares fact definitions with the benchmark labels, so its guard
  accuracy is a consistency check. Only a live provider measurement would show
  whether a model evaluates the facts correctly.
- `bootstrap` imports the guard builder, not the evaluation feature, so feature
  boundaries are unchanged.
