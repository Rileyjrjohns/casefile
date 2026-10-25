import json
from decimal import Decimal

import httpx
import pytest

from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel
from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.llm import LlmDecisionModel
from casefile.features.decisioning.adapters.outbound.local import LocalDecisionModel
from casefile.features.decisioning.adapters.outbound.wire import DecisionSettings, StructuredReply
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.shared.result import Err, Ok


def question(operation: str = "choose", purpose: str = "recommendation") -> Question:
    result = Question.create(
        operation,
        purpose,
        json.dumps({"signal_count": 1, "has_profile": True}),
        ("close", "escalate", "request_information")
        if operation == "choose"
        else ("unsupported", "supported")
        if operation == "score"
        else (),
    )
    assert isinstance(result, Ok)
    return result.value


def test_invalid_questions_and_answers_are_business_errors() -> None:
    for operation, options in (("unknown", ()), ("choose", ()), ("score", ("one",))):
        assert isinstance(Question.create(operation, "risk", "{}", options), Err)
    assert isinstance(Question.create("probability", "unknown", "{}"), Err)
    assert isinstance(Question.create("probability", "risk", "not json"), Err)
    q = question()
    for choice, confidence, probabilities in (
        ("invented", 0.9, (0.1, 0.8, 0.1)),
        ("close", float("nan"), (0.8, 0.1, 0.1)),
        ("close", 0.9, (0.8, 0.8, 0.1)),
        ("close", 0.9, (0.1, 0.8, 0.1)),
    ):
        assert isinstance(Answer.create(q, choice, confidence, probabilities, "test", Usage()), Err)


@pytest.mark.asyncio
async def test_fake_implements_all_three_operations() -> None:
    model = FakeDecisionModel()
    assert (await model.choose(question())).value == "escalate"
    p = await model.probability(question("probability", "guard"))
    assert p.value == 0
    s = await model.score(question("score", "signal"))
    assert 0 <= float(s.value) <= 1


class Risk:
    def predict(self, state_json: str) -> tuple[float, str]:
        return 0.82, "risk-v1"


@pytest.mark.asyncio
async def test_local_uses_risk_port_and_preserves_provenance() -> None:
    q = question("probability", "risk")
    answer = await LocalDecisionModel(Risk()).probability(q)
    assert answer.value == 0.82
    assert answer.model == "risk_model@risk-v1"
    assert answer.usage == Usage()


class StructuredModel:
    def quote(self, question: Question) -> Usage:
        return Usage(1024, Decimal(".01"))

    async def generate(self, question: Question) -> StructuredReply:
        return StructuredReply(
            '{"value":"escalate","confidence":0.8,"probabilities":[0.1,0.8,0.1]}',
            "test-llm",
            Usage(20, Decimal(".001")),
        )


@pytest.mark.asyncio
async def test_llm_validates_structured_output() -> None:
    model = LlmDecisionModel(StructuredModel())
    assert (await model.choose(question())).value == "escalate"
    assert model.quote(question()).tokens == 1024


@pytest.mark.asyncio
async def test_jev_uses_official_schema_and_validates_closed_choices() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/systemone"
        assert body["questions"]["decision"]["type"] == "choice"
        assert set(body["questions"]["decision"]["criteria"]) == set(question().options)
        return httpx.Response(
            200,
            json={
                "model": "jev-test",
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": "escalate",
                        "confidence": 0.7,
                        "probabilities": {
                            "close": 0.1,
                            "escalate": 0.8,
                            "request_information": 0.1,
                        },
                    }
                },
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    settings = DecisionSettings(
        "https://api.typesafe.ai/v1/systemone", "jev-test", "test", Decimal("1"), Decimal("0")
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = JevDecisionModel(client, settings)
        answer = await model.choose(question())
    assert answer.value == "escalate"
    assert answer.usage.tokens == 15
    assert answer.usage.cost == Decimal(".000012")
