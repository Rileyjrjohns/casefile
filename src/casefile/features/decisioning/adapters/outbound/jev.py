"""TypeSafe System One adapter, using the published choice/noul/score wire schema."""

import json
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from casefile.features.decisioning.adapters.outbound.wire import (
    SYSTEM,
    AnswerDocument,
    DecisionSettings,
    instructions,
    post,
    quote,
)
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage


class Choice(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


class Probability(BaseModel):
    type: Literal["noul"]
    noul: float


class Score(BaseModel):
    type: Literal["score"]
    score: float
    confidence: float
    probabilities: dict[str, float]
    legend: dict[str, str]


class Counters(BaseModel):
    model_config = ConfigDict(strict=True)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class Response(BaseModel):
    model: str
    answers: dict[str, Choice | Probability | Score]
    usage: Counters


@dataclass(frozen=True, slots=True)
class JevDecisionModel:
    client: httpx.AsyncClient
    settings: DecisionSettings

    def body(self, question: Question) -> dict[str, object]:
        spec: dict[str, object] = {
            "type": {"choose": "choice", "probability": "noul", "score": "score"}[
                question.operation
            ],
            "instructions": SYSTEM + " " + instructions(question),
        }
        if question.operation == "choose":
            spec["criteria"] = {option: option for option in question.options}
        elif question.operation == "score":
            spec["criteria"] = list(question.options)
        return {
            "model": self.settings.model,
            "state": json.loads(question.state_json),
            "questions": {"decision": spec},
        }

    def quote(self, question: Question) -> Usage:
        return quote(self.body(question), self.settings)

    async def _call(self, question: Question) -> Answer:
        # A single remaining option is already determined, so skip the paid call; the Jev API itself
        # accepts one option (criteria need only one property).
        if question.operation == "choose" and len(question.options) == 1:
            return AnswerDocument(
                value=question.options[0], confidence=1, probabilities=[1.0]
            ).answer(question, "singleton-route", Usage())
        response = Response.model_validate_json(
            await post(self.client, self.settings, self.body(question))
        )
        if set(response.answers) != {"decision"}:
            raise ValueError("Jev returned unexpected question IDs.")
        result = response.answers["decision"]
        if isinstance(result, Choice) and question.operation == "choose":
            if set(result.probabilities) != set(question.options):
                raise ValueError("Jev returned an invalid choice distribution.")
            document = AnswerDocument(
                value=result.choice,
                confidence=result.confidence,
                probabilities=renormalized([result.probabilities[o] for o in question.options]),
            )
        elif isinstance(result, Probability) and question.operation == "probability":
            document = AnswerDocument(
                value=result.noul, confidence=abs(2 * result.noul - 1), probabilities=[]
            )
        elif isinstance(result, Score) and question.operation == "score":
            keys = {str(i) for i in range(len(question.options))}
            if set(result.probabilities) != keys or result.legend != dict(
                enumerate_strings(question.options)
            ):
                raise ValueError("Jev changed the requested rubric.")
            probabilities = renormalized(
                [result.probabilities[str(i)] for i in range(len(question.options))]
            )
            position = sum(i * p for i, p in enumerate(probabilities))
            # The reported score derives from unrounded probabilities; each level may move it.
            if abs(result.score - position) > ROUNDING_STEP * len(probabilities) ** 2:
                raise ValueError("Jev returned a score inconsistent with its distribution.")
            document = AnswerDocument(
                value=position, confidence=result.confidence, probabilities=probabilities
            )
        else:
            raise ValueError("Jev returned a different decision operation.")
        return document.answer(
            question,
            response.model,
            self.settings.usage(response.usage.input_tokens, response.usage.output_tokens),
        )

    async def choose(self, question: Question) -> Answer:
        return await self._call(question)

    async def probability(self, question: Question) -> Answer:
        return await self._call(question)

    async def score(self, question: Question) -> Answer:
        return await self._call(question)


ROUNDING_STEP = 0.005  # Jev reports probabilities to two decimals.


def renormalized(probabilities: list[float]) -> list[float]:
    """Absorb two-decimal rounding drift; a larger gap is a malformed distribution."""
    total = sum(probabilities)
    if total <= 0 or abs(total - 1) > ROUNDING_STEP * len(probabilities) + 1e-9:
        raise ValueError("Jev returned a distribution beyond rounding drift.")
    return [p / total for p in probabilities]


def enumerate_strings(options: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((str(i), option) for i, option in enumerate(options))
