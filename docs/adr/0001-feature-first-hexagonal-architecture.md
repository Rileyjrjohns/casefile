# ADR 001 — Feature-first hexagonal architecture

- Status: accepted
- Date: 2026-10-24

## Context

Casefile must separate investigation rules, human decisions and integration
details. Simulated tools, models, storage and transports will change without
changing business invariants. The repository must support testing these rules
without network access or infrastructure.

## Decision

Organize `src/casefile/features/` by capability: `case_intake`, `investigation`,
`review`, `evaluation`, `risk_scoring` and `decisioning`.

Each feature contains:

- `domain/`: entities, value objects, invariants and business errors;
- `application/`: use cases and the ports they need;
- `adapters/inbound/`: transports that invoke use cases;
- `adapters/outbound/`: port implementations;
- `tests/`: feature tests using fakes for ports.

The domain depends only on the standard library and the `shared` kernel.
The application depends on its own domain and `shared`, without a framework.
Adapters depend on inner layers. Features do not import other features.
The `shared` kernel stays minimal and independent of features.

Only `bootstrap` wires adapters and use cases. Features communicate through
ports or events wired there. Inbound and outbound adapters do not import each
other directly. Domain dataclasses will be immutable; Pydantic will remain at
the integration boundary.

The contracts in `pyproject.toml` run through import-linter in `make check` and
CI. They cover layers, features, composition, third-party dependencies and
typing imports. Tests introduce forbidden imports into a temporary package to
verify that they are rejected. The contract in `quality/contracts.py` uses
Python's standard library module list to distinguish standard library modules
from third-party packages, without maintaining a manual list of vendors.
A second contract rejects standard-library modules that perform I/O in these
layers ([ADR 027](./0027-no-io-standard-library-in-core-layers.md)).
It remains a development tool, excluded from the application wheel.

## Alternatives considered

- A global organization by layer simplifies the initial directory structure
  but scatters changes to a single capability across the repository.
- Allowing features to import one another reduces initial wiring but encourages
  coupling and cycles.
- Microservices would separate deployments, with premature network and
  operational costs for this project.

## Consequences

Rules can be tested with fakes and integrations can be replaced. The directory
structure contains more small packages, and wiring in `bootstrap` requires
explicit code. `shared` must not become a collection of feature-specific logic.

The checks cover static imports; they do not prove the absence of conceptual
coupling or detect dynamic imports. Dynamic imports that bypass boundaries
are forbidden.
