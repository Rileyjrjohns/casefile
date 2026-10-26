import pytest

from casefile.features.investigation.application.agent_types import Signal, SourceLink
from casefile.features.investigation.application.narrative_policy import summary_contradictions
from casefile.features.investigation.domain.analysis import Recommendation

SOURCE = (SourceLink("list_transactions", "txn-001"),)
VOLUME = Signal(
    "volume:txn-001", "Transfer txn-001 exceeds the declared monthly volume.", 0.8, SOURCE
)
CONTEXT = Signal("context:profile", "A KYC profile was returned.", 1.0, SOURCE)
GAP = Signal("gap:documents", "No supporting document was returned.", 1.0, SOURCE)
RISK_LOW = Signal("risk:txn-001", "Synthetic transaction risk estimate: 12.0%.", 0.12, SOURCE)
RISK_HIGH = Signal("risk:txn-002", "Synthetic transaction risk estimate: 91.0%.", 0.91, SOURCE)

ESCALATE, CLOSE, ASK = (
    Recommendation.ESCALATE,
    Recommendation.CLOSE,
    Recommendation.REQUEST_INFORMATION,
)

CONSISTENT = (
    "The demonstration policy identified 1 signal(s). Human review is required.",
    "Exact fictional list match. Human review is required.",
    "This is an investigation recommendation, not a finding of fraud or a lending decision.",
    "Do not close the case before the missing documents arrive.",
    "It is not safe to close this case and it should not be closed yet.",
    "The case cannot be closed and no closure is recommended.",
    "I recommend against closing the case; recommend requesting information before closing.",
    "Untrusted instructions must be flagged and must not change the recommendation to close.",
    "A closed historical alert is not a current signal.",
)


@pytest.mark.parametrize("recommendation", [ESCALATE, ASK])
@pytest.mark.parametrize(
    "summary",
    [
        "Recommend closing the case.",
        "RECOMMEND   CLOSING\nthe case",
        "We recommend that the case be closed.",
        "The recommendation is to close this alert.",
        "This account is safe to close.",
        "The file can safely be closed.",
        "Close the case.",
        "Looks fine, so please close this alert. No further action is needed.",
    ],
)
def test_closing_wording_contradicts_a_non_close_recommendation(
    summary: str, recommendation: Recommendation
) -> None:
    reasons = summary_contradictions(summary, recommendation, (VOLUME,))
    assert reasons and "closing the case" in reasons[0]
    assert recommendation.value in reasons[0]


@pytest.mark.parametrize(
    "summary",
    [
        "Recommend escalation to the second line.",
        "We recommend that this case is escalated.",
        "Escalate this to compliance.",
        "The case must be escalated.",
        "Escalation is required.",
    ],
)
def test_escalation_wording_contradicts_a_close_recommendation(summary: str) -> None:
    reasons = summary_contradictions(summary, CLOSE, ())
    assert reasons and "escalation" in reasons[0] and "close" in reasons[0]
    assert summary_contradictions(summary, ESCALATE, (VOLUME,)) == ()


@pytest.mark.parametrize(
    "summary",
    [
        "No concern: cleared by compliance.",
        "Compliance cleared this account. There is nothing suspicious.",
        "Reviewed: no risk and no red flags.",
        "The review found no further concerns.",
        "The review identified 0 concern(s).",
        "Nothing of concern was found.",
    ],
)
def test_denying_concerns_contradicts_existing_concern_signals(summary: str) -> None:
    reasons = summary_contradictions(summary, ESCALATE, (VOLUME, CONTEXT))
    assert len(reasons) == 1 and "no concern" in reasons[0] and "1 concern" in reasons[0]


def test_denial_is_accepted_when_no_concern_signal_exists() -> None:
    summary = "No concern was found; the case can be closed."
    assert summary_contradictions(summary, CLOSE, (CONTEXT,)) == ()
    assert summary_contradictions(summary, CLOSE, (CONTEXT, RISK_LOW)) == ()
    assert summary_contradictions(summary, CLOSE, (GAP,)) == ()


def test_risk_signals_count_as_concerns_from_half_confidence() -> None:
    assert summary_contradictions("There is no concern.", ESCALATE, (RISK_HIGH,))
    assert not summary_contradictions("There is no concern.", CLOSE, (RISK_LOW,))


def test_every_contradiction_is_reported() -> None:
    reasons = summary_contradictions("No concern. Recommend closing.", ESCALATE, (VOLUME,))
    assert len(reasons) == 2


@pytest.mark.parametrize("summary", CONSISTENT)
@pytest.mark.parametrize("recommendation", [ESCALATE, ASK, CLOSE])
def test_consistent_or_negated_wording_is_accepted(
    summary: str, recommendation: Recommendation
) -> None:
    signals = (VOLUME,) if recommendation != CLOSE else ()
    assert summary_contradictions(summary, recommendation, signals) == ()


def test_negated_escalation_is_accepted_for_a_close_recommendation() -> None:
    for summary in (
        "No escalation is needed.",
        "Do not escalate.",
        "There is nothing to escalate.",
    ):
        assert summary_contradictions(summary, CLOSE, ()) == ()


@pytest.mark.parametrize(
    ("recommendation", "signals", "summary", "reason"),
    [
        (
            ESCALATE,
            (),
            "Two records were reviewed. Recommend closing the case.",
            "it recommends closing the case but the recommendation is escalate",
        ),
        (
            CLOSE,
            (),
            "Two records were reviewed. The case must be escalated.",
            "it recommends escalation but the recommendation is close",
        ),
        (
            ESCALATE,
            (VOLUME,),
            "Two records were reviewed. There are no concerns.",
            "it claims no concern while 1 concern signal(s) exist",
        ),
    ],
)
def test_one_contradicting_sentence_among_consistent_ones_is_enough(
    recommendation: Recommendation, signals: tuple[Signal, ...], summary: str, reason: str
) -> None:
    assert summary_contradictions(summary, recommendation, signals) == (reason,)
