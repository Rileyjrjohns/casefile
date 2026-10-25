"""Learned tabular risk plus an explicit policy for unsupported task semantics."""

from dataclasses import dataclass, field

from casefile.features.decisioning.adapters.outbound.fake import FakeDecisionModel, answer
from casefile.features.decisioning.application.ports import RiskPredictor
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage


@dataclass(frozen=True, slots=True)
class LocalDecisionModel:
    risk: RiskPredictor
    policy: FakeDecisionModel = field(default_factory=FakeDecisionModel)

    def quote(self, question: Question) -> Usage:
        return Usage()

    async def choose(self, question: Question) -> Answer:
        return await self.policy.choose(question)

    async def probability(self, question: Question) -> Answer:
        if question.purpose != "risk":
            return await self.policy.probability(question)
        probability, version = self.risk.predict(question.state_json)
        return answer(question, probability, model=f"risk_model@{version}")

    async def score(self, question: Question) -> Answer:
        return await self.policy.score(question)
