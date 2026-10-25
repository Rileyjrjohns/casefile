# ADR 002 — Result for business errors, exceptions for technical failures

- Status: accepted
- Date: 2026-10-24

## Context

An invalid run transition, a conclusion without a source or a budget overrun
are expected business outcomes. They must be visible in signatures and handled
by use cases. Storage failures or unreadable transport responses require a
different handling mechanism.

## Decision

Operations that can fail for a business reason will return `Result[T, E]`, with
two immutable variants: success containing `T`, or failure containing a typed
business error `E`. The core type is defined in `shared/result.py`, without a
third-party library.

Business errors will be explicit values belonging to each feature. Callers
will handle the variants before using the result. A total operation, with no
possible business failure, may return its value directly. An invalid value
will not be made valid by substituting a default; its validating factory will
return a business error.

Exceptions remain reserved for unexpected technical failures. They will be
caught at appropriate boundaries to log the cause, apply a recovery policy
where relevant and move the run to `FAILED` if needed. A business decision
will not be inferred automatically from a technical failure.

Inbound adapters will translate business errors into protocol-specific
responses; HTTP, MCP and vendor exceptions will not be part of the domain.
No blanket `except Exception` will silently convert a programming error into
a business rejection.

## Alternatives considered

- Exceptions for every rejection make failure paths less visible in signatures
  and mix expected errors with technical failures.
- `None` or a boolean loses the cause and information needed by the client.
- A functional types library would add a dependency to the core for an initial
  need already covered by dataclasses and a generic union.

## Consequences

Signatures document business failures, and mypy can check their value types.
Callers must handle more branches explicitly; Python typing alone does not
guarantee that every result is handled. Tests and code review remain necessary.

Domain factories follow this convention. Objects whose construction requires
validation use `FactoryOnly` from `shared/immutable.py`: their raw constructor
is disabled. Calling that constructor is a programming error (`TypeError`);
passing an invalid business value to the factory produces `Err`. Private
construction and evolution methods are reserved for the domain implementation.

A total operation, such as creating a run from an already validated budget,
returns its value directly. Recording usage that exceeds the budget returns
`Ok` with the new run in `FAILED`: recording the usage succeeded, and the run's
business failure remains explicit in its state.
