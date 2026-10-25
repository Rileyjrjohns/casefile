# ADR 015: Typed decisions rather than free-form generation

Status: Accepted. Supersedes the temporary decision policy boundary in ADR 008.

Non-language decisions use the `DecisionModel` choose/probability/score port.
The narrative model writes summaries and justifications only. Jev, a structured
LLM baseline, local risk plus explicit policy, and a fake implement the port.
Purpose-specific configuration selects providers; bootstrap preserves feature
independence. Application invariants never delegate authorization or final human
approval to a model.

Every decision has a quote, durable reservation, validated output, settled usage
and exact replay event. Jev follows the official System One schema. Its Noul
certainty is derived explicitly; calibration metrics use outcome probabilities.
Narrative routing chooses only configured endpoints.

Comparisons use identical questions from the immutable case suite. Offline
provider fixtures validate contracts, with explicit synthetic provenance. They
cannot establish vendor performance. A separately budgeted manual workflow
collects real provider metrics when credentials are configured. Local tabular
risk is evaluated on separate held-out transaction labels.
