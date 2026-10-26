"""Jev reports two-decimal probabilities whose sum can drift from one by a rounding step."""

import math
from decimal import Decimal

import httpx
import pytest

from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.wire import DecisionSettings
from casefile.features.decisioning.domain.decisions import Answer, Question
from casefile.shared.result import Ok

SETTINGS = DecisionSettings(
    "https://api.typesafe.ai/v1/systemone", "jev-test", "test", Decimal("1"), Decimal("0")
)
ROUTES = ("list_alerts", "list_investigator_notes", "search_sanctions")
RUBRIC = ("unsupported", "partly supported", "fully supported")


def question(operation: str, options: tuple[str, ...]) -> Question:
    result = Question.create(
        operation, "tool_route" if operation == "choose" else "signal", "{}", options
    )
    assert isinstance(result, Ok)
    return result.value


async def ask(question: Question, answer: dict[str, object]) -> Answer:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-test",
                "answers": {"decision": answer},
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        model = JevDecisionModel(client, SETTINGS)
        if question.operation == "choose":
            return await model.choose(question)
        return await model.score(question)


@pytest.mark.asyncio
@pytest.mark.parametrize("probabilities", [(0.84, 0.06, 0.09), (0.84, 0.06, 0.11)])
async def test_a_rounded_choice_distribution_is_renormalized(
    probabilities: tuple[float, ...],
) -> None:
    answer = await ask(
        question("choose", ROUTES),
        {
            "type": "choice",
            "choice": "list_alerts",
            "confidence": 0.79,
            "probabilities": dict(zip(ROUTES, probabilities, strict=True)),
        },
    )
    assert answer.value == "list_alerts"
    assert math.isclose(sum(answer.probabilities), 1, abs_tol=1e-9)
    assert answer.probabilities[0] == pytest.approx(0.84 / sum(probabilities))


@pytest.mark.asyncio
async def test_a_distribution_beyond_rounding_drift_is_rejected() -> None:
    with pytest.raises(ValueError, match="distribution"):
        await ask(
            question("choose", ROUTES),
            {
                "type": "choice",
                "choice": "list_alerts",
                "confidence": 0.5,
                "probabilities": dict(zip(ROUTES, (0.6, 0.1, 0.1), strict=True)),
            },
        )


@pytest.mark.asyncio
async def test_a_rounded_score_is_recomputed_from_the_renormalized_rubric() -> None:
    probabilities = (0.33, 0.33, 0.33)
    answer = await ask(
        question("score", RUBRIC),
        {
            "type": "score",
            "score": 1.0,
            "confidence": 0.3,
            "probabilities": {str(i): p for i, p in enumerate(probabilities)},
            "legend": {str(i): label for i, label in enumerate(RUBRIC)},
        },
    )
    assert math.isclose(sum(answer.probabilities), 1, abs_tol=1e-9)
    assert answer.value == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_a_score_far_from_its_own_distribution_is_rejected() -> None:
    with pytest.raises(ValueError, match="score"):
        await ask(
            question("score", RUBRIC),
            {
                "type": "score",
                "score": 1.8,
                "confidence": 0.3,
                "probabilities": {"0": 0.5, "1": 0.3, "2": 0.2},
                "legend": {str(i): label for i, label in enumerate(RUBRIC)},
            },
        )
