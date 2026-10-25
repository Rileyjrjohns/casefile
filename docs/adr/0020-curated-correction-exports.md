# ADR 020: Export committed corrections as curation candidates

Status: accepted.

## Context

The evaluation feature already stores promoted human corrections, but the review
screen needs an explicit path from a committed correction to a usable export.
Changing a review must never overwrite the original machine analysis or silently
modify reference annotations.

## Decision

Expose promotion through HTTP and a button on committed corrected conclusions.
Require explicit confirmation and the existing curator access port. The fixed
local demo reviewer has this role. A deterministic ID derived from review and
conclusion IDs makes repeated submissions idempotent using existing repository
constraints. No database schema change is needed.

Export a versioned JSON bundle marked `requires_curation`, containing the
correction, original case, analysis, trace and review, with human expected
statements and recommendation. Rejected conclusions are omitted from expected
output; original citations remain intact. Both memory and PostgreSQL use the
same application command. Restart acceptance verifies durable exports.

## Consequences

The UI supports the complete correction-to-candidate path without a second review
or automatic changes to golden files. A curator still decides how to turn the
bundle into independently reviewed fixtures. Promotion is not retraining or
proof of correctness. It grants no additional model authority, and production
identity and access management remain outside this local demo.
