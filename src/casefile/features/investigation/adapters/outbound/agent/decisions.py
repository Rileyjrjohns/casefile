"""Strict serialization and exact replay of metered typed decisions."""

from pydantic import TypeAdapter

from casefile.features.investigation.adapters.outbound.agent.codec import decode, encode
from casefile.features.investigation.adapters.outbound.agent.replay import ReplayTape
from casefile.features.investigation.application.agent_types import TraceEvent
from casefile.features.investigation.application.decisions import DecisionRequest, DecisionResponse
from casefile.features.investigation.domain.budget import BudgetUsage


class DecisionCodec:
    def request_json(self, value: DecisionRequest) -> str:
        return encode(TypeAdapter(DecisionRequest), value)

    def response_json(self, value: DecisionResponse) -> str:
        return encode(TypeAdapter(DecisionResponse), value)

    def response(self, payload: str) -> DecisionResponse:
        return decode(TypeAdapter(DecisionResponse), payload)


class ReplayDecisionGateway:
    def __init__(self, events: tuple[TraceEvent, ...]) -> None:
        self.tape = ReplayTape(events)
        self.codec = DecisionCodec()

    def quote(self, request: DecisionRequest) -> BudgetUsage:
        event = self.tape.peek(
            f"decision.{request.purpose}.{request.key}", self.codec.request_json(request)
        )
        if event.reserved is None:
            raise ValueError("Replay decision reservation is absent.")
        return event.reserved

    async def execute(self, request: DecisionRequest) -> DecisionResponse:
        event = self.tape.consume(
            f"decision.{request.purpose}.{request.key}", self.codec.request_json(request)
        )
        if event.output_json is None:
            raise ValueError("Replay decision response is absent.")
        return self.codec.response(event.output_json)
