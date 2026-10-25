# ADR 010 — Reserve budget before external calls

- Status: accepted
- Date: 2026-10-24

## Context

Counting usage only after a model or tool call permits work that the run could
not afford. Provider failures can leave actual usage unknown, while successful
calls may use less than their maximum allowance.

## Decision

Add explicit `Run.reserve` and `Run.settle` operations. Reservation checks the
next increment before invocation. If it would exceed a limit, fail the run
without adding unperformed usage. Persist an accepted reservation with the
call's `started` event before making the call.

On a completed call, replace that reservation with reported usage atomically
with the response. Settlement cannot release more than the current reserved
usage. If actual usage exceeds a run limit, retain the actual counters, mark the
run failed and perform no further calls. Preserve exact decimal arithmetic.

Unknown usage retains the full reservation. Retrying a known failed attempt
requires a fresh reservation, so repeated failures cannot reset the budget.
The execution journal ensures a completed call is settled only once in normal
orchestration. An interrupted call without a recorded outcome stops the run on
restart, as described in ADR 008.

The model port quotes locally. The HTTP adapter reserves request byte count,
framing allowance and maximum completion tokens at the higher of two explicit
configured prices. No provider price is assumed. The request sets an output
token limit and reported usage is checked afterward. The reservation is an
application estimate, not a guarantee that an arbitrary provider cannot report
or charge more. Unexpected usage cannot undo an external charge.

## Alternatives considered

- Charging only reported usage allows an unaffordable first call.
- Permanently charging every maximum hides actual successful usage and makes
  evaluation costs misleading.
- Releasing reservations on timeouts assumes the provider did no work, which
  the client cannot establish.

## Consequences

Known usage and retained uncertain reservations are separately visible in the
trace. The aggregate's usage is conservative when failures are unresolved.
Costs require consistent currency and current deployment-supplied rates.
Tests cover preflight refusal, settlement, reported overruns, uncertain attempts,
replay accounting and atomic rollback of reservation writes.
