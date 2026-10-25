# Portfolio demo acceptance

The delivered scope is a runnable, fictional investigation demo with sourced
findings, typed provider decisions, explicit human review, durable correction
candidates and reproducible regression evaluation. It does not perform real
banking operations or make lending decisions.

## Start and inspect

1. From the repository root, run `docker compose up --build` with Docker running.
2. Open `http://localhost:8080`. The three original cases run automatically; the
   three extended banking dossiers start when requested.
3. Select CF–001 for identity disambiguation, CF–002 for conflicting income
   documents, or CF–003 for the supplier-account diversion investigation.
4. Open source links and expand **How this analysis was produced**. Without a
   configured provider, demo rules and the template summary cost zero. Jev only
   handles the explicitly selected decision routes; its actual response model
   and small nonzero estimated costs are retained in each run.

To test intake, create a case through `/docs`, the HTTP API or inbound MCP. A valid
fictional subject such as `demo-customer-002` has seeded evidence. Creation does
not invent new customer documents. Reload the screen to see the new case.

## Complete the human-review loop

1. Start an investigation and wait for **Awaiting your review**.
2. Inspect its evidence. Validate, correct or reject every conclusion.
3. For a correction, supply replacement text and a reason. Select the final
   recommendation, tick the personal-review confirmation and submit.
4. The run closes with the human decision; its original analysis remains intact.
5. On a corrected conclusion, choose **Save regression candidate**, then
   **Download candidate**. The JSON contains the original evidence and analysis,
   committed review and human expectations, marked `requires_curation`.
6. Restart the services. The review, source links, candidate and download remain
   available. A new investigation creates a separate history entry.

Saving the candidate does not modify the 30 frozen references or retrain a model.
Curating independent expectations is a deliberate human responsibility.

## Verified checks — October 24, 2026

| Check | Result |
| --- | --- |
| `make check` | 526 Python tests and 28 UI tests passed; lint, formatting, strict typing, five architecture contracts and frontend build passed |
| Coverage | 93.83% overall; domain and application both exceed the 90% gates |
| `make eval` | 30/30 historical cases; offline typed-adapter contract comparison passed |
| Versioned evidence | Monthly totals, month/currency boundaries, repeated transaction IDs, unresolved screening and legacy restart compatibility tested |
| Live Jev on the two new paths | Escalate for cumulative transfers; request information for unresolved screening; USD 0.000084378 combined estimate |
| Fresh Compose database | Migrations, seed loading, six local MCP providers, API and frontend started with an empty environment file and no provider key |
| Browser acceptance | Create via API, investigate, inspect evidence, correct, confirm, save and download; original analysis preserved; desktop and 390-pixel mobile checks passed |
| Six seeded scenarios | Expected recommendations and every cited source verified on the fresh installation, including all three extended banking dossiers |
| PostgreSQL restart | Corrected review, candidate bundle and original source records persisted |
| Existing local installation | Updated API/frontend; six cases and seven complete investigation/review payloads unchanged; recorded Jev provenance verified in desktop/mobile UI |

The fresh-start check used an isolated Compose project and fresh database volume
with separate loopback ports. Docker's dependency/build cache was available;
this was not a timed installation on a clean host. Test reviews were made only
against isolated fictional cases. Existing local investigations are not rejudged
or rewritten by an upgrade.

The historical reference suite explicitly runs `findings-v1`. Current runs use
`findings-v2`; separate tests and the dated live observations cover its additions.
See [evaluations](evaluations.md), [provider measurements](measurements/jev-2026-10-24.md)
and [known limitations](known-limitations.md) for interpretation boundaries.

Production authentication, real banking connectors, operational compliance
validation and a live narrative-LLM comparison are outside this completed demo
scope. They are not prerequisites for running or reviewing the portfolio project.
