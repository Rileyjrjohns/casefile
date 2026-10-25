"""Explicit demonstration policy, never presented as a learned classifier."""

import json
from dataclasses import dataclass

from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.features.decisioning.domain.injection import looks_like_instruction
from casefile.shared.result import Err


def answer(
    question: Question,
    value: str | float,
    *,
    model: str = "demo-policy-v1",
    confidence: float | None = None,
    usage: Usage | None = None,
) -> Answer:
    if question.operation == "choose":
        probabilities = tuple(1.0 if v == value else 0.0 for v in question.options)
        certainty = 1.0
    elif question.operation == "score":
        position = float(value)
        lower = int(position)
        probabilities = tuple(max(0.0, 1 - abs(i - position)) for i in range(len(question.options)))
        certainty = max(probabilities) if lower >= 0 else 0.0
    else:
        probabilities = ()
        certainty = abs(2 * float(value) - 1)
    result = Answer.create(
        question,
        value,
        certainty if confidence is None else confidence,
        probabilities,
        model,
        usage or Usage(),
    )
    if isinstance(result, Err):
        raise ValueError(result.error)
    return result.value


def guard_verdict(state: dict[str, object]) -> float:
    """Allow only an in-scope, permitted, first call; any missing or malformed fact denies."""
    allowed = state.get("tool_allowed")
    called = state.get("already_called")
    scope = state.get("query_scope")
    return float(
        allowed is True and called is False and scope in {"case_subject", "case_kyc_legal_name"}
    )


@dataclass(frozen=True, slots=True)
class FakeDecisionModel:
    def quote(self, question: Question) -> Usage:
        return Usage()

    async def choose(self, question: Question) -> Answer:
        state = json.loads(question.state_json)
        match question.purpose:
            case "priority":
                value = "high" if state.get("kind") == "sanctions_match" else "normal"
            case "alert_type":
                value = str(state.get("kind", "unusual_transaction"))
            case "tool_route":
                value = question.options[0]
            case "model_route":
                value = "primary"
            case "recommendation":
                value = (
                    "escalate"
                    if state.get("signal_count", 0)
                    else "request_information"
                    if state.get("evidence_gaps", False)
                    else "close"
                    if state.get("has_profile", False)
                    else "request_information"
                )
            case _:
                raise ValueError("Demonstration policy does not support this choice.")
        return answer(question, value)

    async def probability(self, question: Question) -> Answer:
        state = json.loads(question.state_json)
        if question.purpose == "guard":
            value = guard_verdict(state)
        elif question.purpose == "injection":
            value = float(looks_like_instruction(str(state.get("text", ""))))
        elif question.purpose == "risk":
            value = 0.5
        else:
            raise ValueError("Demonstration policy does not support this probability.")
        return answer(question, value)

    async def score(self, question: Question) -> Answer:
        state = json.loads(question.state_json)
        confidence = {
            "volume": 0.8,
            "sanctions": 0.9,
            "kyc": 0.7,
            "instruction": 0.6,
            "finding": 1.0,
            "context": 1.0,
            "gap": 1.0,
        }.get(state.get("signal_kind"), 0.5)
        return answer(question, confidence * (len(question.options) - 1))
