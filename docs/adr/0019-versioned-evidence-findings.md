# ADR 019: Version evidence findings independently of frozen labels

Status: accepted.

## Context

The original rules overlook several smaller transfers exceeding monthly volume
and treat an unmatched screening response as if the open alert were resolved.
Cases 009 and 015 deliberately preserve those historical limitations. Changing
their labels or replay responses would erase useful regression evidence.

## Decision

New investigations use `findings-v2`. It aggregates unique EUR transaction IDs
within UTC calendar months, cites every included record and the KYC baseline,
and avoids duplicating an individual excessive-transfer finding. The baseline
is explicitly assumed to be EUR because the historical schema has no currency.
Other currencies create an evidence gap and are excluded from synthetic risk.
An open sanctions alert without a returned list entry creates an identification
gap. It does not assert a sanctions match or a resolved false positive.
The three document-based banking policies retain their scoped comparisons.

The journal records the findings version before gathering evidence. Restarts
reuse it; a previously started journal without this marker retains v1. Golden
replay and typed benchmark questions explicitly select v1. Current behavior,
exact replay and restart compatibility have separate tests.

## Consequences

The historical score remains comparable and is clearly labeled as v1. New rules
can evolve without rewriting previous evidence or decisions. A tool snapshot
is not a complete monthly statement, and these fictional rules establish no
real screening or fraud-detection effectiveness. Closure constraints apply to
the resulting concerns and gaps; only the investigator makes a final decision.
