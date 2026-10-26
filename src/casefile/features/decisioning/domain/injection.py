"""Deterministic heuristic that flags instruction-like text inside untrusted evidence.

This is a documented pattern set, not a classifier: it catches common phrasings and paraphrases
of directives aimed at the agent, and it will miss obfuscated or novel wording. Real-model
detection is equally fallible, so detection only adds a signal and never replaces review.
"""

import re
import unicodedata

_VERBS = r"(?:ignore|disregard|forget|override|bypass)"
_FILLER = r"(?:(?:the|your|my|all|any|of|every)\s+)*"
_SCOPE = r"(?:previous|prior|above|earlier|preceding|all|any)"
_ADDRESSEE = r"(?:agent|assistant|model|ai|llm)"
_NOUNS = r"(?:instructions?|guidance|directions?|prompts?|guidelines?)"

# Applied to normalized text: lower case, ASCII-folded punctuation, single spaces, no apostrophes.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        (
            "override_prior",
            rf"\b{_VERBS} {_FILLER}{_SCOPE} (?:\w+ )?(?:{_NOUNS}|rules)\b"
            rf"|\b{_VERBS} (?:(?:the|your|these|those) )+{_NOUNS}\b",
        ),
        ("system_notice", r"\bsystem (?:notice|prompt|message|override|directive)\b"),
        (
            "addresses_agent",
            rf"\bto the {_ADDRESSEE}\b|\byou are now\b|\bas the {_ADDRESSEE}\b"
            rf"|\b(?:dear|hey|attention|message for the) {_ADDRESSEE}\b"
            r"|\bnew (?:instructions|rules)\b"
            r"|\byou (?:must|should|will|shall) (?:now )?"
            r"(?:state|say|write|report|conclude|recommend|respond|answer|output|cite|mark|close)\b",
        ),
        (
            "outcome_directive",
            r"\brecommend(?:s|ing)? (?:closing|closure|to close)\b"
            r"|\bclose (?:the|this) (?:case|alert|file|investigation)\b"
            r"|\bmark (?:\w+ ){0,3}?as (?:cleared|clean|safe|resolved|approved|legitimate)\b"
            r"|\b(?:do not|dont) (?:escalate|flag|report|raise|mention|disclose)\b"
            r"|\b(?:state|say|declare|conclude|report) that (?:there is )?no (?:concern|risk)\b"
            r"|\b(?:approve|clear) (?:\w+ ){0,2}?automatically\b"
            r"|\bomit (?:the )?(?:citations|sources|references)\b",
        ),
        ("citation_directive", r"\bcite (?:the )?(?:records?|sources?|entry|entries)\b"),
    )
)


def normalize(text: str) -> str:
    """Fold case, width and escapes so a pattern sees one canonical spelling."""
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    folded = re.sub(r"\\[nrt]", " ", folded)
    folded = re.sub(r"['\u2019`]", "", folded.casefold())
    return " ".join(re.sub(r"[^0-9a-z]+", " ", folded).split())


def injection_signals(text: str) -> tuple[str, ...]:
    """Names of the pattern families that match, in a stable order."""
    canonical = normalize(text)
    return tuple(name for name, pattern in PATTERNS if pattern.search(canonical))


def looks_like_instruction(text: str) -> bool:
    return bool(injection_signals(text))
