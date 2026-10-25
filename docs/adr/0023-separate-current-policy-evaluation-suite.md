# ADR 023: Separate current-policy evaluation suite

Status: accepted.

## Context

The 30 frozen cases in `evals/golden/` were written in one batch, and their
expected statements copy the rule code. The runner pinned `findings-v1`, while
every new investigation uses `findings-v2` (monthly EUR aggregation, currency
gaps, unresolved-screening gaps) and the three banking workflows with the
documents tool. Thirteen of the 30 cases have no signal, only three alert kinds
appear and no dossier is covered. The CI gate therefore never exercised the
policy that production runs, and its perfect scores held by construction.
A related reporting flaw showed 1.000 precision and recall for a run that
failed or never completed whenever no signal was expected.

## Decision

- **Keep the frozen suite and relabel it.** `evals/golden/`, `evals/replay/` and
  the keys of `evals/thresholds.toml` are unchanged. Its report is titled the
  *findings-v1 regression profile* and records `findings_policy: findings-v1`.
  A test pins its dataset fingerprint and threshold values.
- **Add a separate current-policy suite** under `evals/current/` (`golden/`,
  `replay/`, `thresholds.toml`), run with `findings-v2`. It has its own strict
  thresholds: precision, recall and recommendation accuracy 1.0, invalid source
  rate 0, every run completing, and `min_cases` equal to the case count so a case
  cannot be dropped silently. The schema's own `min_cases >= 30` floor is
  unchanged. Case ids are `current-NNN`; the golden schema accepts both prefixes.
- **Parameterize the runner by profile.** `AgentCaseRunner` takes the findings
  policy explicitly and defaults to the current one; the legacy profile selects
  `findings-v1` by name. A profile couples a label, a directory and a policy.
  Banking kinds receive a six-call tool budget, as in production. `casefile-eval`
  runs both profiles by default, writes `build/evals/` and `build/evals/current/`
  and exits non-zero if either fails; `make eval` and CI keep calling it, and CI
  uploads `build/evals/**`.
- **Anti-tautology rule for authoring.** Each case states its expected signals,
  the records each must cite, the recommendation and a rationale from the
  scenario and the documented policy (agent guide, banking scenarios, ADRs 018,
  019, 021, 022) before it is run. Expected statements may reuse the
  deterministic statement text; signal selection and recommendation must be
  reasoned. A disagreement is investigated: the code is fixed test-first when it
  violates the documented policy, and an expectation is corrected only when it
  was a genuine mistake, with the reason written in the rationale. Writing the
  suite exposed two such disagreements. The demo support score for the monthly
  volume finding was 0.5 instead of the 1.0 the policy and every other
  deterministic finding use; it was fixed in the decisioning adapter. The
  banking workflows dropped an open KYC-mismatch alert and an exact list entry,
  so a consistent dossier could be proposed for closure although ADR 018 makes
  such concerns block closure; they are now carried into the dossier (the list
  entry stays context in the identity workflow, where it is the compared hit).
  Neither expectation was edited.
- **Stricter than the gate.** The gated metrics compare signal keys. A test also
  compares, for every current case, the cited records, statement text and
  confidence, so a wrong citation set cannot hide behind a correct key.
- **Replay fixtures from the deterministic fake narrative model.**
  `casefile-eval-fixtures` runs the real agent over each case's tool snapshots
  with the fake model, records its single narrative exchange with provenance
  `synthetic-fixture` and never overwrites an existing file. `--check` fails when
  a fixture differs from a fresh recording, which a test enforces. No label is
  read, no network is used and the legacy fixtures are out of its scope.
- **Per-case scores of broken runs.** A case whose run did not reach
  `AWAITING_REVIEW` without errors has `null` precision and recall (an em dash in
  Markdown). Its counts still feed the micro averages and the completion gate.

## Alternatives considered

- Editing the frozen cases to v2 would erase the historical comparison and the
  dated vendor measurement that depends on it.
- Computing expected values by running the current policy would reproduce the
  policy and could not detect a regression.
- Raising thresholds in the existing file would have changed frozen keys; a
  second file leaves them untouched.

## Consequences

A perfect score on either suite still measures regression consistency of a
demonstration policy, not fraud accuracy. The new cases are authored by the same
team that wrote the rules, so they await independent human review like the
frozen ones. The injection labels know the demo pattern set: they guard the
documented paraphrases, benign wording and normalization, not unseen attacks.
Cases that would only document a known limitation are left out rather than
loosening a threshold. Changing the policy or the prompt now requires a
deliberate fixture re-recording and a review of the affected expectations.
Live HTTP evaluations run both suites, which doubles the number of narrative
requests.
