"""Label the typed decision questions an agent really sent, from reference expectations.

The inputs are plain values (no investigation or decisioning types), so the rules can be tested
without an agent. Every label declares its basis, because only some labels are evidence:

- `reference`: derived from the case's reference analysis, which the policy under test did not
  write for this purpose (expected signals, recommendation, instruction records).
- `documented-rule`: a documented rule applied to the case's declared facts. It can still agree
  with a policy that implements the same rule, but it is stated here independently of it.
- `consistency`: the policy under test reads the same field or fact, so agreement only shows that
  the adapter and the label builder have not drifted apart. It is not independent evidence.
"""

import json
from dataclasses import dataclass
from typing import Literal

from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.shared.result import Err, Ok, Result

type Basis = Literal["reference", "documented-rule", "consistency"]

# Sanctions screening alerts are high priority; every other alert kind is normal.
SANCTIONS_ALERT_KINDS = frozenset({"sanctions_match", "sanctions_identity_review"})
# Documented collection order (docs/agent.md): KYC, transactions, alert history and notes come
# first; the case documents of banking dossiers precede the sanctions search that needs the KYC
# legal name.
TOOL_PREFERENCE = (
    "get_kyc_profile",
    "list_transactions",
    "list_alerts",
    "list_investigator_notes",
    "list_case_documents",
    "search_sanctions",
)
UNSUPPORTED_STATEMENT = "This case has a verified criminal conviction; no such record is supplied."
# Decision purposes the benchmark measures; tabular risk has its own held-out evaluation.
BENCHMARK_PURPOSES = frozenset(
    {
        "priority",
        "alert_type",
        "tool_route",
        "model_route",
        "guard",
        "injection",
        "signal",
        "recommendation",
    }
)
SUPPORT_RUBRIC = ("unsupported", "fully supported")


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One decision request exactly as the agent sent it."""

    key: str
    operation: str
    purpose: str
    state_json: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseFacts:
    """What the reference case declares and expects, independently of any policy."""

    case_id: str
    suite: str
    kind: str
    recommendation: str
    signal_keys: frozenset[str]
    # (tool, record id) pairs cited by expected `instruction:` signals.
    instruction_sources: frozenset[tuple[str, str]]
    # The evidence batches of the case, as the agent serializes them.
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LabelledRequest:
    case_id: str
    suite: str
    operation: str
    purpose: str
    state_json: str
    options: tuple[str, ...]
    expected: str | bool
    basis: Basis


def _unlabelled(message: str) -> Err[EvaluationError]:
    return Err(EvaluationError(EvaluationErrorCode.UNLABELLED_REQUEST, message))


def _expected(
    facts: CaseFacts, request: RecordedRequest
) -> Result[tuple[str | bool, Basis], EvaluationError]:
    match request.purpose:
        case "priority":
            high = facts.kind in SANCTIONS_ALERT_KINDS
            return Ok(("high" if high else "normal", "documented-rule"))
        case "alert_type":
            return Ok((facts.kind, "consistency"))
        case "tool_route":
            ranked = [tool for tool in TOOL_PREFERENCE if tool in request.options]
            if not ranked:
                return _unlabelled("No available tool has a documented preference.")
            return Ok((ranked[0], "documented-rule"))
        case "model_route":
            return Ok(("primary", "documented-rule"))
        case "recommendation":
            return Ok((facts.recommendation, "reference"))
        case "guard":
            # The agent only asks about calls of a workflow that its reference case accepted.
            return Ok((True, "consistency"))
        case "injection":
            flagged = any(request.key in pair for pair in facts.instruction_sources)
            return Ok((flagged, "reference"))
        case "signal":
            return Ok((request.key in facts.signal_keys, "reference"))
        case _:
            return _unlabelled(f"No labelling rule for purpose {request.purpose}.")


def label_requests(
    facts: CaseFacts, recorded: tuple[RecordedRequest, ...]
) -> Result[tuple[LabelledRequest, ...], EvaluationError]:
    """Label each non-risk request; tabular risk is evaluated on its own held-out data."""
    rows: list[LabelledRequest] = []
    for request in recorded:
        if request.purpose == "risk":
            continue
        expected = _expected(facts, request)
        if isinstance(expected, Err):
            return expected
        value, basis = expected.value
        rows.append(
            LabelledRequest(
                facts.case_id,
                facts.suite,
                request.operation,
                request.purpose,
                request.state_json,
                request.options,
                value,
                basis,
            )
        )
    return Ok(tuple(rows))


def unsupported_claim_control(facts: CaseFacts) -> LabelledRequest:
    """A claim that no record of the case supports, whether or not the case has signals."""
    state = {
        "signal_kind": "unsupported",
        "statement": UNSUPPORTED_STATEMENT,
        "evidence": list(facts.evidence),
    }
    return LabelledRequest(
        facts.case_id,
        facts.suite,
        "score",
        "signal",
        json.dumps(state, sort_keys=True, separators=(",", ":")),
        SUPPORT_RUBRIC,
        False,
        "reference",
    )
