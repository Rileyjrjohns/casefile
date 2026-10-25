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
        # A singleton route is already determined; Jev requires at least two choice options.
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
                probabilities=[result.probabilities[o] for o in question.options],
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
            document = AnswerDocument(
                value=result.score,
                confidence=result.confidence,
                probabilities=[result.probabilities[str(i)] for i in range(len(question.options))],
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


def enumerate_strings(options: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((str(i), option) for i, option in enumerate(options))
