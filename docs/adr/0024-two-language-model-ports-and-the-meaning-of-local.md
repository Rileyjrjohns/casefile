# ADR 024: Two language-model ports, two HTTP clients, and what `local` means

Status: accepted.

## Context

Two ports are both named `LanguageModel`, and two adapters both call a Chat
Completions-compatible endpoint:

| Feature | Port | Asks for | Adapter |
| --- | --- | --- | --- |
| `investigation` | `application/agent_types.py`: `quote(ModelRequest)`, `generate(ModelRequest) -> ModelResponse` | Narrative text for a set of approved signals, with sources | `adapters/outbound/agent/http_model.py` (`HttpLanguageModel`, `ModelSettings`) |
| `decisioning` | `application/ports.py`: `quote(Question)`, `generate(Question) -> StructuredReply` | One typed decision as strict JSON, used by `LlmDecisionModel` | `adapters/outbound/llm.py` (`HttpLanguageModel`, `DecisionSettings`) |

The duplication looks accidental and was undocumented. It is also easy to
misread `LocalDecisionModel`: the name suggests a local model for every decision,
while only tabular risk is learned.

## Decision

- **Keep two ports.** They have different inputs, outputs, validation and
  failure semantics. The narrative port returns free text that is checked against
  approved signals and sources and rendered deterministically when no model is
  configured. The decision port must return a closed option, a probability or a
  rubric score with a distribution, and never free text. Features are independent
  (ADR 001), so neither may import the other's port, and `bootstrap` maps between
  them where a shared concept is needed.
- **Keep two HTTP clients.** Both are small (request body, bounded streaming read,
  URL and credential checks, usage counters) but their bodies, response schemas and
  error types differ. The only place both features could share code is
  `shared/`, which the import-linter contract `Third-party packages stay outside
  domain, application and shared` keeps stdlib-only, so a common httpx helper
  cannot live there. A third package for it would be a new dependency edge between
  features that the independence contract forbids. We accept a few dozen
  duplicated lines of transport code and keep the shared rules (HTTPS or loopback
  HTTP, no URL credentials, redirects disabled, a one-megabyte cap, a ten-second
  default timeout) documented in [decisions.md](../decisions.md) and exercised by
  each client's own tests.
- **Test the decision side with one contract.** `tests/contracts/test_decision_model.py`
  runs the same behavioural assertions against the fake policy, `local`, `jev`
  over a mock transport and `llm` over the real decision HTTP client, so a new
  adapter is held to the port rather than to ad hoc tests.
- **`local` means demo policy plus a learned risk model.** `LocalDecisionModel`
  answers the `risk` probability with the versioned tabular model (ADR 014) and
  delegates every other purpose (priority, type, route, guard, injection, signal,
  recommendation) to the explicit demonstration policy implemented by the fake.
  It is not a local language model, a classifier for those purposes, or evidence
  of what a learned model would score on them. Reports label its rows "measured
  local policy" for that reason.

## Consequences

- A change to Chat Completions handling may need to be made twice. Reviewers
  should check both clients; the tests of each client pin the shared safeguards.
- If a third consumer appears, extracting a transport adapter owned by a single
  new feature and reached through a port becomes the next option, and needs its
  own ADR.
- Benchmark and documentation wording must call `local` "demo policy plus learned
  risk" rather than "local model".
