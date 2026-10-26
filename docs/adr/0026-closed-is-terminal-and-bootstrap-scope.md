# ADR 026: CLOSED is terminal, and what bootstrap hosts

Status: accepted.

## Context

The original run rule said `FAILED` is reachable "from every state". The domain
implemented that literally: `Run.fail` accepted a `CLOSED` run and produced a
`FAILED` snapshot that still carried the human decision. The persistence
adapters (`valid_checkpoint` in the in-memory journal and the SQL run store)
rejected exactly that transition, so the terminal-state rule lived in adapters
and the domain disagreed with the storage it was meant to be the source of
truth for. `Run.close` also protected an invariant (a run awaiting review has an
analysis) with an `assert`, which disappears under `python -O`.

Separately, `bootstrap/` is the largest package (HTTP routes, the MCP server,
the `Runtime` facade, the evaluation runner and the decision benchmark) and the
reason was not written down.

## Decision

- **Read "FAILED from every state" as "from every non-terminal state".**
  `PENDING`, `GATHERING`, `ANALYZING` and `AWAITING_REVIEW` can fail. `CLOSED`
  is terminal: a committed human decision is final and closing is the only
  outcome that carries one, so `Run.fail` on a closed run returns
  `Err(INVALID_TRANSITION)`. Failing a run that is already `FAILED` still
  returns it unchanged, preserving the first cause. Budget overruns are only
  possible while `GATHERING` or `ANALYZING`, so `consume` and `reserve` are
  unaffected.
- **Keep the adapter checks.** The journal and SQL store keep rejecting
  `CLOSED -> FAILED`, backward moves and decision changes. They now duplicate a
  domain rule on purpose: persisted rows can be written by other code paths
  or older versions, and a stale in-memory snapshot must never overwrite a
  review closure.
- **No assertion for invariants.** `Run.close` returns
  `Err(INVALID_TRANSITION)` when a run in `AWAITING_REVIEW` has no analysis,
  which can only come from a corrupt snapshot. Domain invariants are never
  expressed as `assert`; business and corruption cases go through `Result`.
- **Bootstrap hosts cross-feature bridges and transports.** Feature inbound
  adapters keep the validated documents (`OpenCaseDocument`, `StartRunDocument`,
  `ReviewDocument`, `PromoteReferenceDocument`) because each is about one
  feature. Bootstrap hosts what has to know several features or a transport:
  the FastAPI routes, the MCP server (both are inbound adapters over the same
  `Runtime`), the `Runtime` facade, the in-memory and PostgreSQL compositions,
  the evaluation runner and the decision benchmark. The import-linter contracts
  forbid a feature importing another, so any code that joins case intake,
  investigation, review and evaluation can only live there.

## Bootstrap review for movable logic

Each `Runtime` method was checked for logic that involves a single feature and
could move into it without cross-feature imports.

- `start_run` reads the case kind (intake) to choose the banking tool budget
  (investigation): cross-feature.
- `get_run` joins the run, its review and the curated references
  (investigation, review, evaluation): cross-feature.
- `promote_reference`, `reference_bundle` and `review` bridge review, evaluation
  and investigation through ports.
- `source` only touches investigation, but decodes stored events with
  `AgentJsonCodec`, which is an outbound adapter. The contract
  "Inbound and outbound adapters are wired by bootstrap" forbids an inbound
  adapter from importing it, so this lookup stays in bootstrap too.
- `unwrap` and `TransportFailure` translate every feature's error codes into
  HTTP statuses, a transport concern shared by the HTTP and MCP adapters.

No pure single-feature logic remained, so nothing was moved.

## Consequences

- The domain transition matrix, its Hypothesis properties and the storage
  contracts now agree: `CLOSED` and `FAILED` are both absorbing.
- A late technical failure after a review closed the run can no longer relabel
  it; the worker already ignores `CLOSED` runs, and any such attempt surfaces
  as an invalid transition instead of a silent state change.
- If bootstrap keeps growing, the next step is to split the transports and the
  `Runtime` facade into sub-packages, not to move cross-feature code into a
  feature.
