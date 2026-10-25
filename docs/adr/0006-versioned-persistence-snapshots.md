# ADR 006 — Versioned JSONB snapshots and relational integrity

- Status: accepted
- Date: 2026-10-24

## Context

Domain entities are immutable and constructed through validating factories.
Loading them directly as ORM models would couple their lifecycle to the
database and risk bypassing their invariants. Analyses and reviews also have
nested structures that will evolve.

## Decision

Adapters use SQLAlchemy 2 in async mode, with one session per operation and
explicit transactions. Alembic migrations describe the schema statically;
they do not use current models to recreate an older revision retroactively.

Cases use relational columns. Runs, reviews and reference examples retain
identifiers and concurrency fields in columns and their complete snapshot
in a JSONB document with `schema_version: 1`. `Decimal` costs are serialized
as strings to preserve precision. UUID identifiers, timestamps with time zones,
source references and decisions are preserved.

Pydantic models live exclusively in adapters. They reject unknown versions
and unexpected fields. Reconstruction uses public domain factories and
transitions; it does not call private constructors. A reconstructed run is
compared with the input document to reject contradictory combinations of
fields and state. Columns and documents are also compared on read. Storage
corruption is a technical error.

Each feature owns its ORM models. The composition root combines their metadata
and adds foreign keys between features without one feature importing another.
`alembic check` verifies that this metadata does not drift from the migrated
schema. Snapshots do not replace future detailed traces of model and tool
calls, which will be added with orchestration.

A run and its job are created together. For a review, the transactional adapter
locks the run, checks its revision and conclusions, updates the run and inserts
the review before committing. Uniqueness violations are translated into
business errors only for known conflicts; other SQL errors remain technical
exceptions and trigger a rollback.

## Alternatives considered

- Using domain dataclasses as ORM models makes invariants depend on hydration
  rules and mutable session objects.
- Serializing with pickle ties data to Python code and does not provide
  explicit validation of the stored format.
- Normalizing every analysis subobject now multiplies tables without an
  identified need for targeted queries.

## Consequences

Snapshots are readable, validated and versioned. Fields used in queries or
constraints remain relational; new columns or tables can be added when usage
justifies them. Any JSON format change will require a version-aware reader or
an explicit data migration.

Testcontainers creates an isolated PostgreSQL 18 database from an image pinned
by digest. Tests cover all run states, decimals, invalid documents, concurrent
transactions and migration round trips. A reconnection test verifies that data
does not depend on the adapters' memory.
