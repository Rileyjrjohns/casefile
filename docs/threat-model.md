# Threat model

## Scope and trust boundaries

Casefile is a local demonstration with fictional data. Untrusted alert text,
evidence fields, provider responses and MCP client arguments enter through typed
adapters. Domain invariants, application authorization, persisted journals and
human review form separate checks. Provider services and MCP clients are not
assumed to be truthful. Database and runtime administrators remain trusted.

| Threat | Controls | Residual limitation |
| --- | --- | --- |
| Instructions hidden in notes or other tool text | Typed read-only tools, data/instruction separation, DecisionModel injection checks, escaped UI text, CSP | Demo detector recognizes one phrase; model-based detection is fallible |
| Closing recommendation despite detected instructions or unresolved concerns | Application-level closure constraint, sourced blocking signals, preserved provider answer, visible policy adjustment | Undetected or incorrectly extracted concerns remain possible; explicit human review is still required |
| Invented or unrelated citations | Records registered only after successful calls; narrative must cover exactly the approved keys and sources | Provenance does not establish factual entailment or authenticity |
| Tool escalation or arbitrary queries | Closed routing options, query validation, authorization independent of guard model, six read-only providers | Real integrations would require per-customer permissions |
| Forged local-model evidence | Versioned JSON weights; recorded inputs/output; source registered only after execution | Trusted files can still be replaced by an administrator; artifacts are not signed |
| Automated human approval | Required per-conclusion actions, explicit confirmation, MCP elicitation, revision check | A dishonest client can pretend a human confirmed; demo has no authenticated identities |
| Replay or stale review | Single-use argument-bound elicitation token, five-minute expiry, optimistic revision, atomic close | Tokens are process-local; reconnect/restart requires new confirmation |
| Duplicate external calls after crash | Durable reservation before call; reuse successful responses; fail on unknown outcome; lease fencing | External billing can exceed provider quotes; failed attempts retain reservations |
| Runaway consumption | Tool/token/cost caps, bounded request/response schemas, worker deadline and finite lease | No global tenant quota or production rate limiter |
| Browser-origin attack | Loopback exposure, trusted Host checks, foreign-Origin write rejection, same-origin nginx proxy | Production needs TLS, identity, authorization and equivalent proxy limits |
| Sensitive-data leakage | Fictional data only; metadata-only logs and traces; explicit remote export/provider opt-in | Run journals contain full evidence and narrative; backups/reports require access controls |
| Model artifact execution | Runtime uses validated JSON, not pickle; training checkpoint tests use weights-only loading | Training dependencies and build supply chain remain trusted |

The demo closes no account, transfers no money and modifies no external banking
system. Recommendations always stop at `AWAITING_REVIEW`. Corrections preserve
original text and cannot silently rewrite the fixed reference suite.

Review identities, TLS, key rotation, encrypted storage, retention, deletion,
backup recovery, audit access controls, rate limits and signed artifacts must be
designed before handling real data. These controls are outside this fictional demo.
