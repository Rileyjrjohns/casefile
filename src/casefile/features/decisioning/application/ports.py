"""Typed decisions and optional tabular prediction are independent ports."""

from dataclasses import dataclass
from typing import Protocol

from casefile.features.decisioning.domain.decisions import Answer, Question, Usage


class DecisionModel(Protocol):
    def quote(self, question: Question) -> Usage: ...
    async def choose(self, question: Question) -> Answer: ...
    async def probability(self, question: Question) -> Answer: ...
    async def score(self, question: Question) -> Answer: ...


class RiskPredictor(Protocol):
    def predict(self, state_json: str) -> tuple[float, str]: ...


@dataclass(frozen=True, slots=True)
class StructuredReply:
    json: str
    model: str
    usage: Usage


class LanguageModel(Protocol):
    def quote(self, question: Question) -> Usage: ...
    async def generate(self, question: Question) -> StructuredReply: ...
