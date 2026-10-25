"""Structured LLM baseline, isolated behind the LanguageModel port."""

import json
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from casefile.features.decisioning.adapters.outbound.wire import (
    SYSTEM,
    AnswerDocument,
    DecisionSettings,
    instructions,
    post,
    quote,
)
from casefile.features.decisioning.application.ports import LanguageModel, StructuredReply
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage


@dataclass(frozen=True, slots=True)
class LlmDecisionModel:
    language_model: LanguageModel

    def quote(self, question: Question) -> Usage:
        return self.language_model.quote(question)

    async def _call(self, question: Question) -> Answer:
        response = await self.language_model.generate(question)
        return AnswerDocument.model_validate_json(response.json).answer(
            question, response.model, response.usage
        )

    async def choose(self, question: Question) -> Answer:
        return await self._call(question)

    async def probability(self, question: Question) -> Answer:
        return await self._call(question)

    async def score(self, question: Question) -> Answer:
        return await self._call(question)


class Message(BaseModel):
    content: str
    refusal: str | None = None


class Choice(BaseModel):
    finish_reason: Literal["stop"]
    message: Message


class Counters(BaseModel):
    prompt_tokens: int = Field(strict=True, ge=0)
    completion_tokens: int = Field(strict=True, ge=0, le=1024)
    total_tokens: int = Field(strict=True, ge=0)


class Completion(BaseModel):
    model: str
    choices: list[Choice] = Field(min_length=1, max_length=1)
    usage: Counters


@dataclass(frozen=True, slots=True)
class HttpLanguageModel:
    client: httpx.AsyncClient
    settings: DecisionSettings

    def body(self, question: Question) -> dict[str, object]:
        return {
            "model": self.settings.model,
            "max_completion_tokens": 1024,
            "messages": [
                {"role": "system", "content": SYSTEM + " " + instructions(question)},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "state": json.loads(question.state_json),
                            "operation": question.operation,
                            "options": question.options,
                        }
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "typed_decision",
                    "strict": True,
                    "schema": AnswerDocument.model_json_schema(),
                },
            },
        }

    def quote(self, question: Question) -> Usage:
        return quote(self.body(question), self.settings)

    async def generate(self, question: Question) -> StructuredReply:
        response = Completion.model_validate_json(
            await post(self.client, self.settings, self.body(question))
        )
        usage = response.usage
        if (
            response.choices[0].message.refusal is not None
            or usage.total_tokens != usage.prompt_tokens + usage.completion_tokens
        ):
            raise ValueError("Refused decision or inconsistent usage.")
        return StructuredReply(
            response.choices[0].message.content,
            response.model,
            self.settings.usage(usage.prompt_tokens, usage.completion_tokens),
        )
