"""Heuristic backstop that keeps a model summary from contradicting the analysis.

The recommendation and the conclusions are deterministic; only the summary is free text. This
check rejects a summary that asserts a different outcome or denies that concerns exist. It is a
phrase-level heuristic, not semantic entailment: reworded contradictions can pass, and some
unusual but honest phrasings are rejected. Human review remains the control that matters.
"""

import re
import unicodedata

from casefile.features.investigation.application.agent_types import Signal
from casefile.features.investigation.application.recommendation_policy import counts_as_concern
from casefile.features.investigation.domain.analysis import Recommendation

# Words that negate or defer an outcome when they sit just before it or inside the match.
NEGATORS = frozenset(
    {
        "not",
        "no",
        "never",
        "cannot",
        "without",
        "nor",
        "neither",
        "against",
        "before",
        "until",
        "unless",
        "instead",
        "rather",
        "avoid",
        "prevent",
        "dont",
        "doesnt",
        "isnt",
        "arent",
        "wasnt",
        "wont",
        "wouldnt",
        "shouldnt",
        "couldnt",
        "mustnt",
        "cant",
    }
)
WINDOW = 4

_CLOSE = r"clos(?:e|ed|ing|ure)"
CLOSING = (
    re.compile(rf"\brecommend(?:s|ed|ing)? (?:\w+ ){{0,5}}?(?:{_CLOSE}|dismiss\w*)\b"),
    re.compile(r"\brecommendation (?:is |would be )to close\b"),
    re.compile(r"\b(?:safe|ready|fine|ok|okay) to close\b"),
    re.compile(
        r"\b(?:can|may|could|should|must|will) (?:(?:now|safely|be|simply|then) )*closed?\b"
    ),
    re.compile(r"\bclose (?:the|this) (?:case|alert|file|investigation)\b"),
    re.compile(r"\bno further action (?:is )?(?:needed|required|necessary)\b"),
)
ESCALATION = (
    re.compile(r"\brecommend(?:s|ed|ing)? (?:\w+ ){0,5}?escalat(?:e|es|ed|ing|ion)\b"),
    re.compile(
        r"\b(?:should|must|needs? to|requires?|warrants?) (?:\w+ ){0,2}?escalat(?:e|ed|ion)\b"
    ),
    re.compile(r"\bescalation (?:is |are )?(?:recommended|required|warranted|needed|necessary)\b"),
    re.compile(r"^(?:please |then )?escalate\b"),
)
DENIAL = (
    re.compile(
        r"\bno (?:further |remaining |outstanding |material |significant |apparent )?"
        r"(?:concerns?|risks?|red flags?)\b"
        r"(?! (?:signals?|model|scores?|estimates?|scoring|rules?))"
    ),
    re.compile(r"\bnothing (?:suspicious|unusual|concerning|of concern|to worry about)\b"),
    re.compile(r"\bno suspicious\b"),
    re.compile(r"\bcleared by (?:the )?compliance\b|\bcompliance (?:has |have )?cleared\b"),
    re.compile(r"\b(?:fully|already) cleared\b|\ball clear\b"),
    re.compile(r"\b(?:0|zero) concerns?\b"),
)


def normalize(sentence: str) -> str:
    folded = unicodedata.normalize("NFKC", sentence).casefold()
    folded = re.sub(r"['\u2019`]", "", folded)
    return " ".join(re.sub(r"[^0-9a-z]+", " ", folded).split())


def asserts(sentence: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Whether a normalized sentence asserts a pattern without a nearby negation."""
    for pattern in patterns:
        for match in pattern.finditer(sentence):
            before = sentence[: match.start()].split()[-WINDOW:]
            inside = match.group().split()[1:]
            if not any(word in NEGATORS for word in (*before, *inside)):
                return True
    return False


def summary_contradictions(
    summary: str, recommendation: Recommendation, signals: tuple[Signal, ...]
) -> tuple[str, ...]:
    """Reasons the summary conflicts with the recommendation and signals; empty when consistent."""
    sentences = tuple(
        normalize(part) for part in re.split(r"[.!?;\n]+", summary) if normalize(part)
    )
    reasons: list[str] = []
    if recommendation != Recommendation.CLOSE and any(asserts(s, CLOSING) for s in sentences):
        reasons.append(
            f"it recommends closing the case but the recommendation is {recommendation.value}"
        )
    if recommendation == Recommendation.CLOSE and any(asserts(s, ESCALATION) for s in sentences):
        reasons.append("it recommends escalation but the recommendation is close")
    concerns = sum(counts_as_concern(signal) for signal in signals)
    if concerns and any(asserts(s, DENIAL) for s in sentences):
        reasons.append(f"it claims no concern while {concerns} concern signal(s) exist")
    return tuple(reasons)
