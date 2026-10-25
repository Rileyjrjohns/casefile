# ADR 004 — Async application ports and atomic writes

- Status: accepted
- Date: 2026-10-24

## Context

Use cases must work with fakes and then async SQLAlchemy adapters without
importing a framework. Two operations involve multiple writes: starting a run
with its job, and recording a review while closing the run. A partial write
would leave either a run that never starts or a review and run that contradict
one another.

## Decision

Use cases receive immutable commands and return `Result` values. I/O ports are
async `Protocol` definitions. Business errors remain typed values; technical
failures propagate through use cases and will be handled at future execution
boundaries. Callers supply command identifiers; transports may generate them
on the server. `Clock` supplies timestamps to keep tests deterministic.

### Opening cases and starting runs

`OpenCase` trims leading and trailing whitespace from the alert identifier,
subject and description. It rejects empty values and requires a timestamp with
a time zone. `CaseRepository.add` guarantees uniqueness of both the case ID and
the normalized alert ID. Alert identifiers must be globally unique in the input
source; adding multiple sources will require qualifying them. A duplicate
returns an explicit error and does not replace the case.

`StartRun` checks that the case exists through its own `CaseLookup` port.
`RunStore.enqueue` atomically stores a `PENDING` run and its job. It rejects
an already used run ID and a second active run for the same case. A new run may
be created after `CLOSED` or `FAILED`, using a new identifier. The worker claims
jobs with a lease; its investigation executor will be wired during the agent
stage. See ADR 005 for delivery and retries.

Stored snapshots have an integer revision starting at 1. Saves compare the
expected revision and increment it. They reject backward state transitions
and direct closure: closure goes through the review port.

### Review and closure

`RecordReview` follows these steps:

1. Check the investigator's access to the run and explicit confirmation.
2. Load the original conclusions and revision of the `AWAITING_REVIEW` run.
3. Reject a revision that differs from the one displayed to the investigator.
4. Build and validate one decision per conclusion using the stored originals.
   The command contains no original text supplied by the client.
5. Call `ReviewGateway.commit` to record the review and close the run in a
   single atomic operation.

The commit rechecks state, revision, original texts and review ID uniqueness.
A conflict or failure changes neither the review nor the run. Repeated
submission after closure is rejected; it does not produce a second decision.
A correction preserves the initial analysis and separately records the
corrected text and rationale.

The authorization port does not replace authentication. Future inbound adapters
must obtain the identity from a trusted mechanism and pass it to the use case.
They must not trust a `reviewer_id` freely chosen in the request body.

### Promoting corrections

`PromoteCorrection` checks promotion access and reloads the correction from a
review committed with its run. A simple validation or rejection cannot be
promoted as a correction. The candidate retains the case, run, review,
conclusion, original and replacement text, rationale, review author and
promotion author, with their timestamps.

`ReferenceRepository.add_once` guarantees a single promotion per
`(review_id, conclusion_id)` pair. Repeating the operation returns the existing
example without rewriting its provenance, even if a new reference ID is
supplied. Reusing a reference ID for another correction is rejected.

These candidates remain in a separate store. They do not automatically modify
`evals/golden/`; a later export must gather the case and tool data needed for
a complete reference case.

### Feature boundaries and fakes

Each feature declares its own ports and types. `bootstrap/memory.py` contains
the wiring between them: case lookup, translation of a review into a
`HumanDecision`, and projection of a correction into the evaluation domain.
Features never import one another, and `shared` remains limited to identifiers,
the clock and generic mechanisms.

In-memory adapters support tests and local composition. The review/run commit
prepares and validates both snapshots before publishing them, without an
`await` between writes. Failure injection happens before anything is published.
This guarantee applies to a single asyncio event loop and a single process;
it is not a durable transaction and does not protect against process termination.

PostgreSQL adapters enforce the same contracts through transactions, uniqueness
constraints and row locks. Contract tests are parameterized for fakes and
PostgreSQL through Testcontainers, without changing the use cases. SQL triggers
inject a failure during job or review insertion to verify that the first write
is rolled back.

## Alternatives considered

- Calling `save_run` and then `enqueue_job` in the use case leaves an orphaned
  run when the second operation fails.
- Saving a review and closing the run separately cannot guarantee a consistent
  decision in the presence of failures or concurrency.
- Importing another feature's repository directly simplifies the first
  integration but breaks the boundaries checked by import-linter.

## Consequences

Use cases remain testable with fakes and without network access. Ports express
the guarantees adapters must provide. Wiring features requires explicit
translations; the durable review adapter shares the transaction for the
records involved. Tests cover the complete workflow, business rejections,
revision conflicts and the absence of partial writes.
