"""The guard state is derived from verifiable facts, never from a precomputed verdict."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from casefile.features.investigation.application.agent_types import CaseContext, EvidenceBatch
from casefile.features.investigation.application.guard_policy import (
    guard_denial_reasons,
    guard_state,
)
from casefile.features.investigation.application.tools import KycProfile, ToolName

SUBJECT = "demo-customer-002"
OTHER = "demo-customer-003"
LEGAL_NAME = "Alder Trading Ltd"


def context(kind: str = "unusual_transaction") -> CaseContext:
    return CaseContext(SUBJECT, "alert-1", kind, "Fictional transfer")


def profile(customer_id: str = SUBJECT, legal_name: str = LEGAL_NAME) -> KycProfile:
    return KycProfile(
        "kyc-1",
        customer_id,
        legal_name,
        "FR",
        "trader",
        Decimal(1000),
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def kyc(customer_id: str = SUBJECT, legal_name: str = LEGAL_NAME) -> EvidenceBatch:
    return EvidenceBatch(ToolName.KYC, customer_id, (profile(customer_id, legal_name),))


@pytest.mark.parametrize(
    "tool",
    [ToolName.KYC, ToolName.TRANSACTIONS, ToolName.ALERTS, ToolName.NOTES],
)
def test_subject_query_on_a_core_tool_is_allowed_and_in_scope(tool: ToolName) -> None:
    state = guard_state(tool, SUBJECT, context(), ())
    assert state == {
        "tool": tool.value,
        "query": SUBJECT,
        "subject_id": SUBJECT,
        "query_scope": "case_subject",
        "tool_allowed": True,
        "already_called": False,
    }


def test_query_for_another_customer_is_unrelated() -> None:
    state = guard_state(ToolName.TRANSACTIONS, OTHER, context(), ())
    assert state["query_scope"] == "unrelated"
    assert state["subject_id"] == SUBJECT


def test_sanctions_query_equal_to_the_returned_legal_name_is_in_scope() -> None:
    state = guard_state(ToolName.SANCTIONS, LEGAL_NAME, context(), (kyc(),))
    assert state["query_scope"] == "case_kyc_legal_name"
    assert state["tool_allowed"] is True


def test_sanctions_query_that_differs_from_the_legal_name_is_unrelated() -> None:
    state = guard_state(ToolName.SANCTIONS, "Someone Else", context(), (kyc(),))
    assert state["query_scope"] == "unrelated"


def test_legal_name_of_a_profile_for_another_customer_does_not_grant_scope() -> None:
    evidence = (kyc(OTHER, "Birch Holdings"),)
    state = guard_state(ToolName.SANCTIONS, "Birch Holdings", context(), evidence)
    assert state["query_scope"] == "unrelated"
    assert state["tool_allowed"] is True


def test_legal_name_is_not_a_valid_scope_for_a_customer_scoped_tool() -> None:
    state = guard_state(ToolName.NOTES, LEGAL_NAME, context(), (kyc(),))
    assert state["query_scope"] == "unrelated"


def test_sanctions_is_not_allowed_before_a_kyc_profile_was_returned() -> None:
    state = guard_state(ToolName.SANCTIONS, LEGAL_NAME, context(), ())
    assert state["tool_allowed"] is False
    assert state["query_scope"] == "unrelated"
    empty = (EvidenceBatch(ToolName.KYC, SUBJECT, ()),)
    assert guard_state(ToolName.SANCTIONS, LEGAL_NAME, context(), empty)["tool_allowed"] is False


@pytest.mark.parametrize(
    ("kind", "allowed"),
    [
        ("payment_fraud", True),
        ("credit_document_fraud", True),
        ("sanctions_identity_review", True),
        ("unusual_transaction", False),
        ("sanctions_match", False),
        ("kyc_inconsistency", False),
    ],
)
def test_documents_are_allowed_only_for_banking_kinds(kind: str, allowed: bool) -> None:
    state = guard_state(ToolName.DOCUMENTS, SUBJECT, context(kind), ())
    assert state["tool_allowed"] is allowed


def test_a_tool_already_called_in_this_run_is_flagged() -> None:
    state = guard_state(ToolName.KYC, SUBJECT, context(), (kyc(),))
    assert state["already_called"] is True
    other = guard_state(ToolName.NOTES, SUBJECT, context(), (kyc(),))
    assert other["already_called"] is False


def test_state_never_carries_a_precomputed_verdict() -> None:
    state = guard_state(ToolName.KYC, SUBJECT, context(), ())
    assert not {"authorized", "allowed", "verdict", "decision"} & set(state)


def test_denial_reasons_name_each_failing_fact() -> None:
    state = guard_state(ToolName.SANCTIONS, "Someone Else", context(), ())
    assert guard_denial_reasons(state) == (
        "tool not allowed for this workflow",
        "unrelated query scope",
    )
    repeated = guard_state(ToolName.KYC, SUBJECT, context(), (kyc(),))
    assert guard_denial_reasons(repeated) == ("tool already called in this run",)
    assert guard_denial_reasons(guard_state(ToolName.KYC, SUBJECT, context(), ())) == ()


def test_malformed_state_is_reported_as_missing_facts() -> None:
    assert guard_denial_reasons({"tool": "get_kyc_profile"}) == ("guard facts missing or invalid",)


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({"query_scope": "elsewhere"}, id="unknown-scope"),
        pytest.param({"query_scope": None}, id="no-scope"),
        pytest.param({"tool_allowed": "yes"}, id="allowed-as-text"),
        pytest.param({"tool_allowed": 1}, id="allowed-as-number"),
        pytest.param({"already_called": "no"}, id="called-as-text"),
        pytest.param({"already_called": 0}, id="called-as-number"),
        pytest.param({"tool_allowed": None}, id="allowed-missing"),
        pytest.param({"already_called": None}, id="called-missing"),
    ],
)
def test_one_malformed_fact_is_enough_to_fail_closed(broken: dict[str, object]) -> None:
    state: dict[str, object] = dict(guard_state(ToolName.KYC, SUBJECT, context(), ()))
    assert guard_denial_reasons(state) == ()
    assert guard_denial_reasons({**state, **broken}) == ("guard facts missing or invalid",)
