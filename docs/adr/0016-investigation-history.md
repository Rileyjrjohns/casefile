# ADR 016: Stable investigation history and explicit new attempts

Status: Accepted.

Sorting random UUIDs can display an older completed run after a newer one.
The run-store contract now returns newest-created runs first. PostgreSQL assigns
a database identity sequence at insertion, indexed with case ID. Memory stores
use dictionary insertion order. Subsequent checkpoints, reviews and retry
schedules do not reorder history. Sequence gaps after rolled-back inserts are
expected and have no business meaning. The sequence stays persistence metadata;
domain snapshots, narrative fixtures and external run identifiers are unchanged.

Migration 0003 backfills existing runs using the first recorded event time,
otherwise the job's available time, with UUID as a deterministic tie-breaker.
The original enqueue instant was not previously stored; historical order may be
approximate. New values start after the backfilled maximum, regardless of clocks.
Downgrading removes only the ordering metadata and index, preserving run data.

The review screen defaults to the active investigation, otherwise the latest
completed run. A selector exposes previous runs and their persisted human
reviews. Selection clears unsaved actions, confirmation and outstanding evidence
requests. A new investigation is an explicit command available only when no run
is active; the server's atomic uniqueness constraint remains authoritative.
Closed runs and their human decisions are never reopened or overwritten.
