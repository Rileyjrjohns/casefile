# ADR 017: Sourced banking dossiers within the investigation product

Status: Accepted.

Casefile needs richer payment-fraud, credit-document and identity-screening
examples while retaining its evidence-gathering and human-review workflow.
Credit eligibility and automated banking actions remain outside the product.

Add a read-only `DocumentsTool` with immutable structured records, backed by memory
and a sixth standalone MCP provider. Preserve the existing five record types and
their serialized representations so historical journals and exact replays still
decode. A separate additive `banking_seed.json` keeps the frozen regression data
unchanged. New kinds explicitly select the extended six-tool plan; original cases
retain the five-tool plan and request shapes.

Comparison rules belong to the investigation application and produce sourced
concerns, context and information gaps. They compare only linked records with
compatible amounts, periods, currencies and provenance. The model scores evidence
support and recommends an outcome; deterministic rules retain arithmetic and
source validation. Scoped provider instructions distinguish mitigating evidence
from concerns without changing historical provider request bodies.

The combined server idempotently creates three additional cases without queuing
runs. The user starts each investigation. Bootstrap defaults the tool budget to
six for these kinds only when the caller omitted that limit. Explicit limits,
existing reviews and existing run histories are preserved. No database migration
is needed: the existing versioned case/run snapshots and execution journal store
the added enum values and evidence records.

Identity and credit-document investigations do not use the tabular transaction
risk model. The scenarios do not justify interpreting that model as an identity,
document-authenticity or solvency classifier. Structured documents are fixture
records, not uploaded PDFs or authenticated external evidence.
