# ADR 022: Deterministic conclusion text and summary consistency backstop

Status: accepted.

## Context

A probe run showed three weaknesses in how untrusted text reaches the reviewer.
The demo injection detector was a single literal phrase, so a paraphrased
instruction planted in an investigator note produced no `instruction:` signal.
A compromised narrative model that cited an invented source was rejected, but one
that kept the approved keys and sources and only rewrote the text was accepted:
every conclusion became "No concern: cleared by compliance." and the summary
recommended closing a case whose recommendation was `escalate`. Source validation
establishes provenance, not that a sentence is entailed by its sources.

## Decision

- **Conclusion text is authoritative.** `validate_analysis` builds each
  `Conclusion.statement` from the approved signal's own deterministic statement.
  The model must still return exactly one statement per approved signal key with
  exactly that signal's sources; invented, unrelated, duplicate or missing
  citations still fail the run. The model's per-statement wording is kept in the
  journal (the `model.generate` output) but is never shown as a conclusion. The
  narrative model's role is the summary, as the specification states.
- **Summary consistency backstop.** `application/narrative_policy.py` is a pure
  function over the summary, the effective recommendation and the signals. It
  rejects a summary that recommends closing when the recommendation is `escalate`
  or `request_information`, recommends escalation when the recommendation is
  `close`, or denies concerns ("no concern", "cleared by compliance", "nothing
  suspicious", "no risk") while at least one concern signal exists. Concerns use
  the same predicate as the closure constraint: any signal other than `context:`
  and `gap:`, with `risk:` signals counting from confidence 0.5. Matching is
  case-insensitive and whitespace-normalized, works per sentence, and ignores an
  outcome that a negator ("not", "never", "against", "before", ...) sits next to,
  so "not a finding of fraud or a lending decision" passes. A violation fails the
  run with `Model summary contradicts the analysis: ...` naming each contradiction.
- **Broader demo injection detector.** `decisioning/domain/injection.py` replaces
  the single literal with a normalized pattern set (override verbs with prior
  instructions, system notices, addressing the agent, directives about the outcome
  or about citations). Text is NFKC-folded, stripped of invisible format
  characters and JSON escapes, lower-cased and reduced to words before matching.
  `FakeDecisionModel`, and so `LocalDecisionModel`, use it for the `injection`
  decision. The decision benchmark labels its injection records with the same
  function.

## Consequences

- An obedient or compromised narrative model can no longer change the conclusions
  a reviewer reads, and cannot contradict the recommendation in the summary with
  the listed phrasings.
- Both text checks are heuristics. The detector misses obfuscated or novel
  wording and flags an investigator who legitimately writes a directive-like
  phrase; the backstop passes contradictions that avoid its phrases and rejects
  some honest summaries that quote an instruction without negating it. Neither is
  semantic entailment or a general prompt-injection defence, and detection by a
  real model is fallible too. Failing closed is preferred here: a rejected run
  can be restarted, an accepted contradiction misleads a reviewer.
- The summary is still free text from an untrusted model. Human review, the
  deterministic recommendation and the closure constraint remain the controls.
- Recorded replay requests and responses are unchanged: signals are the same, the
  recorded statements already equal the signal statements, and the recorded
  summaries pass the backstop.
