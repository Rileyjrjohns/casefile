"""Narrative adapter contracts, HTTP validation and exact offline replay."""

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.http_model import (
    HttpLanguageModel,
    ModelProviderError,
    ModelSettings,
)
from casefile.features.investigation.adapters.outbound.agent.models import (
    FakeLanguageModel,
    ReplayLanguageModel,
)
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    LanguageModel,
    ModelRequest,
    Signal,
    SourceLink,
    TraceEvent,
)
from tests.contracts.support import AT


def request() -> ModelRequest:
    return ModelRequest(
        CaseContext("demo-customer-001", "alert-001", "unusual_transaction", "Fictional alert"),
        (),
        (
            Signal(
                "signal-1",
                "A fictional signal.",
                0.8,
                (SourceLink("list_transactions", "txn-001"),),
            ),
        ),
    )


def settings() -> ModelSettings:
    return ModelSettings(
        "https://model.example/v1/chat/completions",
        "configured-model",
        "test-secret",
        Decimal("1"),
        Decimal("2"),
    )


def response_body() -> dict[str, object]:
    return {
        "model": "served-model-v1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": (
                        '{"summary":"A fictional summary.","statements":[{"key":"signal-1",'
                        '"text":"A fictional signal.","sources":[{"tool":"list_transactions",'
                        '"record_id":"txn-001"}]}]}'
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["fake", "replay", "http"])
async def test_language_model_contract(adapter: str) -> None:
    codec = AgentJsonCodec()
    call = request()
    fake = FakeLanguageModel()
    original = await fake.generate(call)
    events = (
        TraceEvent(
            1, "model.generate", "started", AT, codec.request_json(call), reserved=fake.quote(call)
        ),
        TraceEvent(
            2,
            "model.generate",
            "succeeded",
            AT,
            codec.request_json(call),
            codec.response_json(original),
            fake.quote(call),
        ),
    )

    def respond(http_request: httpx.Request) -> httpx.Response:
        assert http_request.headers["Authorization"] == "Bearer test-secret"
        assert b'"response_format"' in http_request.content
        assert b'"max_completion_tokens":1024' in http_request.content
        return httpx.Response(200, json=response_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model: LanguageModel = (
            fake
            if adapter == "fake"
            else ReplayLanguageModel(events, codec)
            if adapter == "replay"
            else HttpLanguageModel(client, settings(), codec)
        )
        quote = model.quote(call)
        result = await model.generate(call)
        assert result.summary and result.model
        assert result.statements[0].sources == call.signals[0].sources
        assert result.statements[0].key == call.signals[0].key
        assert result.input_tokens + result.output_tokens <= quote.tokens
        assert Decimal(0) <= result.estimated_cost <= quote.estimated_cost
        assert codec.response(codec.response_json(result)) == result
        if adapter == "http":
            assert result.estimated_cost == Decimal("0.00014")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ["http", "json", "shape", "truncated", "usage", "extra", "large"]
)
async def test_http_adapter_rejects_invalid_responses(defect: str) -> None:
    def respond(http_request: httpx.Request) -> httpx.Response:
        if defect == "http":
            return httpx.Response(503, text="unavailable")
        if defect == "json":
            return httpx.Response(200, text="not json")
        if defect == "large":
            return httpx.Response(200, content=b"x" * 1_000_001)
        body = response_body()
        if defect == "shape":
            return httpx.Response(200, json={})
        if defect == "usage":
            body["usage"] = {"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 19}
        elif defect == "truncated":
            body["choices"] = [{"finish_reason": "length", "message": {"content": "{}"}}]
        else:
            body["choices"] = [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"summary":"x","statements":[],"recommendation":"close"}'
                    },
                }
            ]
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises((ModelProviderError, httpx.HTTPStatusError)):
            await HttpLanguageModel(client, settings(), AgentJsonCodec()).generate(request())


@pytest.mark.asyncio
async def test_replay_never_falls_back_to_a_live_model() -> None:
    codec = AgentJsonCodec()
    replay = ReplayLanguageModel((), codec)
    with pytest.raises(ValueError):
        replay.quote(request())
    with pytest.raises(ValueError):
        await replay.generate(request())


@pytest.mark.parametrize(
    "url",
    [
        "http://external.example/model",
        "https://name:secret@example.com/x",
        "https://example.com/x?key=secret",
        "file:///tmp/model",
    ],
)
def test_configuration_rejects_unsafe_or_ambiguous_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        replace(settings(), url=url)


def test_configuration_requires_explicit_rates_and_does_not_expose_credentials() -> None:
    assert "test-secret" not in repr(settings())
    with pytest.raises(ValueError):
        ModelSettings.from_environment({})
    with pytest.raises(ValueError):
        replace(settings(), input_cost_per_million=Decimal("NaN"))
