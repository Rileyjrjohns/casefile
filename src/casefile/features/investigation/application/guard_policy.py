"""Pre-tool guard facts derived from the case and this run's evidence, never from a verdict."""

from collections.abc import Mapping, Sequence

from casefile.features.investigation.application.agent_types import CaseContext, EvidenceBatch
from casefile.features.investigation.application.banking_policy import BANKING_KINDS
from casefile.features.investigation.application.tools import KycProfile, ToolName

CORE_TOOLS = frozenset({ToolName.KYC, ToolName.TRANSACTIONS, ToolName.ALERTS, ToolName.NOTES})
SUBJECT_SCOPE = "case_subject"
LEGAL_NAME_SCOPE = "case_kyc_legal_name"
UNRELATED_SCOPE = "unrelated"


def _tool_allowed(tool: ToolName, context: CaseContext, profiles: Sequence[KycProfile]) -> bool:
    if tool in CORE_TOOLS:
        return True
    if tool == ToolName.DOCUMENTS:
        return context.kind in BANKING_KINDS
    return tool == ToolName.SANCTIONS and bool(profiles)


def _query_scope(
    tool: ToolName, query: str, context: CaseContext, profiles: Sequence[KycProfile]
) -> str:
    if query == context.subject_id:
        return SUBJECT_SCOPE
    if tool == ToolName.SANCTIONS and any(
        p.customer_id == context.subject_id and p.legal_name == query for p in profiles
    ):
        return LEGAL_NAME_SCOPE
    return UNRELATED_SCOPE


def guard_state(
    tool: ToolName,
    query: str,
    context: CaseContext,
    evidence: Sequence[EvidenceBatch],
) -> dict[str, str | bool]:
    """Describe the proposed call with facts a provider can check; carry no verdict."""
    profiles = tuple(
        record for batch in evidence for record in batch.records if isinstance(record, KycProfile)
    )
    return {
        "tool": tool.value,
        "query": query,
        "subject_id": context.subject_id,
        "query_scope": _query_scope(tool, query, context, profiles),
        "tool_allowed": _tool_allowed(tool, context, profiles),
        "already_called": any(batch.tool == tool for batch in evidence),
    }


def guard_denial_reasons(state: Mapping[str, object]) -> tuple[str, ...]:
    """Name every failing fact; a malformed state fails closed with a single reason."""
    scope, allowed, called = (
        state.get("query_scope"),
        state.get("tool_allowed"),
        state.get("already_called"),
    )
    if (
        scope not in {SUBJECT_SCOPE, LEGAL_NAME_SCOPE, UNRELATED_SCOPE}
        or not isinstance(allowed, bool)
        or not isinstance(called, bool)
    ):
        return ("guard facts missing or invalid",)
    return tuple(
        reason
        for failed, reason in (
            (not allowed, "tool not allowed for this workflow"),
            (scope == UNRELATED_SCOPE, "unrelated query scope"),
            (called, "tool already called in this run"),
        )
        if failed
    )
