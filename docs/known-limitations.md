# Known limitations

- All accounts, lists, alerts and transaction labels are fictional. No real fraud
  detection, identity resolution or compliance effectiveness is established.
- The local reviewer identity is fixed. An optional bearer gate is not a complete
  authentication system. A malicious MCP client can fake its human interaction.
- Demo injection detection is a normalized pattern set, a heuristic that
  obfuscated or novel wording can evade; detection by a real model is fallible
  too. Source validation checks provenance and signal membership, not the truth of
  natural-language claims. Conclusion text comes from the approved signals, and a
  phrase-level backstop rejects summaries that contradict the recommendation or
  deny existing concerns, but the summary remains model-written text that a
  reworded contradiction can slip past, so human review is still required.
- The tabular model is calibrated only against its synthetic generator. Runtime
  features use incomplete-window proxies and no currency conversion. It cannot
  perform general language decisions; local routing uses explicit policy.
- The 30 frozen cases and the 69 current-policy cases are small, correlated,
  written by the authors of the rules and pending independent human review. The
  current expectations were reasoned from the documented policy before each run,
  but their injection labels know the demo pattern set and a perfect score only
  shows regression consistency.
  Historical regression excludes the new risk signal; separate tests cover its
  complete integration. Synthetic Jev/LLM protocol fixtures are not vendor results.
- Historical `findings-v1` and the frozen corpus retain their documented monthly
  volume and unmatched-screening gaps. New `findings-v2` investigations aggregate
  observed EUR transfers within UTC calendar months and request identification
  evidence for an unresolved open screening alert. The KYC baseline is assumed
  to be EUR; foreign-currency amounts produce an information gap. Returned tool
  snapshots do not establish that the entire month's activity was retrieved.
- The banking workflows apply their document comparisons, flagged instruction-like
  text, open KYC-mismatch alerts and (outside the identity workflow) exact list
  entries. They do not apply the transaction-volume rules, and payment and credit
  documents are compared in EUR only.
- Provider-compatible structured HTTP contracts are tested offline. A
  [single live Jev measurement](measurements/jev-2026-10-24.md) matched 23/30
  reference recommendations, including disagreements on injection-only cases.
  Operational labels encode the demo policy and do not establish real-world
  effectiveness. No live LLM comparison has been measured. Manual workflows
  require explicit credentials, model names and prices for selected providers.
- The demo policy marks only `sanctions_match` alerts high priority, while the
  benchmark's documented rule also treats `sanctions_identity_review` alerts as
  sanctions alerts, so `local` scores 0.889 on priority. Its fixed support scores
  also place neutral evidence above the benchmark's 0.5 boundary (signal accuracy
  0.582). The accuracy floors in `evals/decision-thresholds.toml` record these
  results as regression guards, not as acceptable quality. Offline Jev rows
  replay a genuine `jev-1.14.0` recording made on 2026-10-26; they describe that
  model on synthetic labels at that date, and a repeated identical question keeps
  its first recorded answer. Offline LLM rows still come from synthetic fixtures
  derived from the demo policy.
- Raw decision questions and complete investigations supply different context.
  Reproducing the three injection cases through the API/worker produced two
  escalation recommendations and one request for information. A deterministic
  [closure constraint](adr/0018-recommendation-closure-constraints.md) now blocks
  closing recommendations when detected concerns or missing evidence remain;
  this does not improve the model's raw benchmark score or detect unknown attacks.
- Queue delivery is at least once. There is no dead-letter queue or attempt cap.
  Unknown interrupted calls fail conservatively; a new run must be initiated.
- Memory mode is single-process and transient. MCP pending confirmations are
  process-local. PostgreSQL retains data; production backup/retention policies
  and multi-instance confirmation storage are not provided.
- The frontend is a review screen, not a full case-management administration UI.
  Case creation uses API/MCP. History and new investigations are available in the
  screen. Pre-migration run order is reconstructed from first trace timestamps
  or job schedules, so original enqueue order may be approximate for old data.
- Journals store evidence and model output. Observability intentionally omits
  that content, and exporters are opt-in. No real data should be inserted.
- Correction exports preserve human expectations and original evidence for
  curation. They do not automatically rewrite the frozen corpus, retrain a model
  or establish that the corrected statement is objectively true.
