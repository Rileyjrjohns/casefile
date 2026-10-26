import json

import pytest

from casefile.features.evaluation.domain.decision_questions import (
    CaseFacts,
    LabelledRequest,
    RecordedRequest,
    label_requests,
    unsupported_claim_control,
)
from casefile.shared.result import Err, Ok

TOOLS = (
    "get_kyc_profile",
    "list_transactions",
    "list_alerts",
    "list_investigator_notes",
    "list_case_documents",
    "search_sanctions",
)


def facts(
    kind: str = "unusual_transaction",
    recommendation: str = "escalate",
    signals: frozenset[str] = frozenset({"volume:txn-1"}),
    instructions: frozenset[tuple[str, str]] = frozenset(),
) -> CaseFacts:
    return CaseFacts(
        "case-x", "legacy", kind, recommendation, signals, instructions, ('{"tool":"t"}',)
    )


def request(
    purpose: str,
    operation: str = "choose",
    state: dict[str, object] | None = None,
    options: tuple[str, ...] = (),
    key: str = "k",
) -> RecordedRequest:
    return RecordedRequest(
        key, operation, purpose, json.dumps(state or {}, sort_keys=True), options
    )


def labelled(facts_: CaseFacts, *recorded: RecordedRequest) -> tuple[LabelledRequest, ...]:
    result = label_requests(facts_, recorded)
    assert isinstance(result, Ok)
    return result.value


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("sanctions_match", "high"),
        ("sanctions_identity_review", "high"),
        ("unusual_transaction", "normal"),
        ("payment_fraud", "normal"),
        ("kyc_inconsistency", "normal"),
    ],
)
def test_priority_is_high_for_sanctions_alerts_only(kind: str, expected: str) -> None:
    (row,) = labelled(facts(kind), request("priority", options=("normal", "high")))
    assert row.expected == expected
    assert row.basis == "documented-rule"


def test_alert_type_is_the_declared_kind_and_is_a_consistency_check() -> None:
    (row,) = labelled(facts("payment_fraud"), request("alert_type", options=("payment_fraud",)))
    assert (row.expected, row.basis) == ("payment_fraud", "consistency")


def test_tool_route_follows_the_documented_preference_among_available_options() -> None:
    available = ("search_sanctions", "list_alerts", "list_investigator_notes")
    (row,) = labelled(
        facts(), request("tool_route", state={"available": list(available)}, options=available)
    )
    assert row.expected == "list_alerts"
    (row,) = labelled(
        facts(), request("tool_route", options=("list_case_documents", "search_sanctions"))
    )
    assert row.expected == "list_case_documents"
    assert row.basis == "documented-rule"


def test_tool_route_with_only_unranked_options_is_not_labelled() -> None:
    assert isinstance(
        label_requests(facts(), (request("tool_route", options=("mystery_tool",)),)), Err
    )


def test_model_route_is_the_primary_profile() -> None:
    (row,) = labelled(facts(), request("model_route", options=("primary", "conservative")))
    assert (row.expected, row.basis) == ("primary", "documented-rule")


def test_recommendation_is_the_reference_recommendation() -> None:
    (row,) = labelled(
        facts(recommendation="request_information"),
        request("recommendation", options=("close", "escalate", "request_information")),
    )
    assert (row.expected, row.basis) == ("request_information", "reference")


def test_guard_requests_sent_by_the_agent_are_accepted_calls() -> None:
    (row,) = labelled(facts(), request("guard", "probability"))
    assert (row.expected, row.basis) == (True, "consistency")


@pytest.mark.parametrize(
    "key,expected",
    [("list_investigator_notes", True), ("note-001", True), ("get_kyc_profile", False)],
)
def test_injection_is_true_for_batches_and_records_cited_by_an_instruction_signal(
    key: str, expected: bool
) -> None:
    case = facts(
        signals=frozenset({"instruction:note-001"}),
        instructions=frozenset({("list_investigator_notes", "note-001")}),
    )
    (row,) = labelled(case, request("injection", "probability", key=key))
    assert (row.expected, row.basis) == (expected, "reference")


def test_signal_is_true_only_for_reference_signal_keys() -> None:
    options = ("unsupported", "fully supported")
    rows = labelled(
        facts(signals=frozenset({"volume:txn-1"})),
        request("signal", "score", key="volume:txn-1", options=options),
        request("signal", "score", key="finding:x", options=options),
    )
    assert [r.expected for r in rows] == [True, False]
    assert {r.basis for r in rows} == {"reference"}


def test_risk_requests_are_excluded_and_unknown_purposes_are_errors() -> None:
    assert labelled(facts(), request("risk", "probability")) == ()
    assert isinstance(label_requests(facts(), (request("narrative"),)), Err)


def test_labelled_rows_carry_the_question_and_case_identity() -> None:
    (row,) = labelled(facts(), request("priority", state={"kind": "x"}, options=("normal", "high")))
    assert (row.case_id, row.suite, row.operation, row.purpose) == (
        "case-x",
        "legacy",
        "choose",
        "priority",
    )
    assert row.options == ("normal", "high")
    assert json.loads(row.state_json) == {"kind": "x"}


def test_unsupported_claim_control_uses_the_agents_signal_state_shape() -> None:
    row = unsupported_claim_control(facts())
    assert (row.operation, row.purpose, row.expected, row.basis) == (
        "score",
        "signal",
        False,
        "reference",
    )
    state = json.loads(row.state_json)
    assert set(state) == {"signal_kind", "statement", "evidence"}
    assert state["signal_kind"] == "unsupported"
    assert state["evidence"] == ['{"tool":"t"}']
    assert row.options == ("unsupported", "fully supported")
    assert row.state_json == json.dumps(state, sort_keys=True, separators=(",", ":"))
