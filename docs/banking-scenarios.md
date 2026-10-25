# Fictional banking investigations

The running API includes three extended dossiers alongside the original three
regression examples. Each new dossier starts without an investigation: select it
in the review screen and choose **Start investigation**. Existing investigations
and human reviews are preserved across upgrades and restarts.

| Scenario | Customer | Evidence to reconcile | Offline policy recommendation |
| --- | --- | --- | --- |
| Supplier payment: disputed bank-account change | `demo-customer-101` | EUR 18,450 order and invoice, changed account instruction, independent supplier callback, previous payments | Escalate |
| Credit application: conflicting income evidence | `demo-customer-102` | EUR 4,200 declared/payslip income, EUR 2,600 salary credits for three matching months, employer response and applicant explanation | Escalate |
| Sanctions alert: same name, different identity | `demo-customer-103` | Matching name, different birth dates and countries, customer identity document and independent registry cross-check | Propose closure for human review |

These are authored scenarios, not real customer cases or independently labeled
model benchmarks. A configured Jev or LLM recommendation can differ from the
offline policy; its answer, model, usage and cost remain visible in the trace.
Nothing approves a loan, executes or blocks a transfer, or applies a legal
sanction. The credit scenario investigates documentary consistency, not solvency.

## Evidence and reasoning

Six read-only ports gather KYC, transactions, alerts, investigator notes, sanctions
and supporting documents. The new `list_case_documents` port returns 16 structured
fictional records: 4 payment records, 9 income records and 3 identity records.
Each contains a stable ID, issuer, timestamp, source-verification designation,
named fields and explanatory text. `independent` describes fixture provenance;
it is not document authentication or a live call to an employer, supplier or registry.
There is no PDF parsing, OCR or access to a real banking database.

The payment case includes evidence consistent with a genuine purchase. A familiar
amount and an approved order do not verify a changed beneficiary account. A
variant with independently confirmed new coordinates is treated as mitigating
evidence rather than an unresolved diversion concern.

The income case compares matching months, employers and currencies. It retains
the applicant's bonus/reimbursement explanation and checks it against the
employer response. A discrepancy is a reason to investigate, not proof of fraud.
Malformed amounts, unverified comparison sources or missing monthly extracts
produce information gaps rather than fabricated numeric findings.

The identity case links the biography to the exact returned fictional list entry.
An independent registry corroborates the customer identity. Different birth dates
and countries support the authored false-positive hypothesis; partial matches,
unverified records and inconsistent identity references require more information.
Matching biographical fields produce a concern for human investigation. This
narrow comparison is not a production sanctions-screening rule.

The stored findings distinguish `finding:` concerns, `context:` corroborating or
mitigating facts, and `gap:` missing verification. Instruction-like text remains
untrusted evidence and is flagged with its source. Context and gaps are not counted
as suspicious findings merely because they appear in the analysis. With no
established concern, an evidence gap leads the offline policy to request information.
All conclusions, including mitigating facts, still require explicit human review.

The local transaction-risk model remains available for the payment case. It is
not applied to the credit-document or identity scenarios, which are outside its
synthetic transaction feature space. Narrative text defaults to deterministic
rendering of sourced findings; configuring Jev alone does not enable a narrative LLM.

## Configuration and compatibility

The combined API/MCP server loads `seed.json` plus `banking_seed.json`. The original
seed, golden corpus and historical request formats remain unchanged. Standalone
evidence providers can opt in with `casefile-demo-mcp documents --banking`; Python
composition uses `connect_demo_tools(banking=True)` or `load_seed(banking=True)`.
The standalone in-memory inbound MCP demo keeps its original three examples.

New alert kinds are `payment_fraud`, `credit_document_fraud` and
`sanctions_identity_review`. Their default run budget allows six evidence calls.
An explicitly supplied smaller budget is never increased. Existing Jev routing
for `priority` and `recommendation` applies to these cases, with scoped dossier
guidance for the final recommendation. Starting a run can therefore make billable
calls when those routes are enabled. No new provider credentials are needed.

Documents are stored in the existing execution journal and served only when
observed in the selected investigation. The screen presents readable fields and
retains an expandable exact JSON record. Historical investigations keep their
original sources, even if future fixtures change.

## Scenario design references

The payment-diversion pattern and independent callback are informed by
[Cybermalveillance.gouv.fr's false-bank-details guidance](https://www.cybermalveillance.gouv.fr/tous-nos-contenus/fiches-reflexes/que-faire-en-cas-de-fraude-au-virement-ou-au-faux-rib/ee028135-3a8b-46d6-87b8-2d79d4edd72a).
Comparing identifying information when resolving a name-screening alert is
described in sections 157–161 of the
[ACPR/Trésor asset-freezing guidelines, March 2026](https://acpr.banque-france.fr/system/files/2026-03/20260318_lignes_directrices_gel_des_avoirs.pdf).
These references inform the scenarios; they do not validate the demo's policies.

Tests cover all three end-to-end investigations, exact offline replay, readable
source rendering, memory/MCP document contracts, missing and inconsistent evidence,
authorized account changes, matching income and partial/full identity matches.
