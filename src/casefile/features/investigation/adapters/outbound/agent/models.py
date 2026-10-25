"""Deterministic narrative fake and exact request-matched replay adapter."""

from decimal import Decimal

from casefile.features.investigation.adapters.outbound.agent.replay import ReplayTape
from casefile.features.investigation.application.agent_types import (
    AgentCodec,
    ModelRequest,
    ModelResponse,
    NarrativeStatement,
    TraceEvent,
)
from casefile.features.investigation.application.banking_policy import BANKING_KINDS, is_concern
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.shared.result import Ok


class FakeLanguageModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def quote(self, request: ModelRequest) -> BudgetUsage:
        result = BudgetUsage.create(0, 512 + request.max_output_tokens, Decimal(0))
        assert isinstance(result, Ok)
        return result.value

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        summary = (
            f"{request.context.description}. The document review identified "
            f"{sum(is_concern(signal) for signal in request.signals)} concern(s) and "
            f"{sum(signal.key.startswith('gap:') for signal in request.signals)} evidence gap(s). "
            "Review the corroborating evidence and source records before deciding. "
            "This is an investigation recommendation, not a finding of fraud or a lending decision."
            if request.context.kind in BANKING_KINDS
            else (
                f"The demonstration policy identified {len(request.signals)} signal(s). "
                "Human review is required."
            )
        )
        return ModelResponse(
            "fake-narrative-v1",
            summary,
            tuple(
                NarrativeStatement(signal.key, signal.statement, signal.sources)
                for signal in request.signals
            ),
            128,
            min(64, request.max_output_tokens),
            Decimal(0),
        )


class ReplayLanguageModel:
    def __init__(self, events: tuple[TraceEvent, ...], codec: AgentCodec) -> None:
        self.tape = ReplayTape(events)
        self.codec = codec

    def _match(self, request: ModelRequest) -> TraceEvent:
        payload = self.codec.request_json(request)
        return self.tape.peek("model.generate", payload)

    def quote(self, request: ModelRequest) -> BudgetUsage:
        event = self._match(request)
        if event.reserved is None:
            raise ValueError("Replay model reservation is missing.")
        return event.reserved

    async def generate(self, request: ModelRequest) -> ModelResponse:
        event = self.tape.consume("model.generate", self.codec.request_json(request))
        if event.output_json is None:
            raise ValueError("Replay model response is missing.")
        return self.codec.response(event.output_json)
