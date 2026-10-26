import json
from pathlib import Path

import httpx
import pytest

from casefile.bootstrap.decision_benchmark import Probe, ReplayTransport, benchmark, settings
from casefile.bootstrap.decisions import configured_decisions
from casefile.features.decisioning.adapters.outbound.jev import JevDecisionModel
from casefile.features.decisioning.adapters.outbound.llm import HttpLanguageModel, LlmDecisionModel
from casefile.features.decisioning.domain.decisions import Answer, Question, Usage
from casefile.shared.result import Err
from tests.contracts.support import value


@pytest.mark.parametrize("state", ["[]", "{} " * 250001, '{"x":NaN}'])
def test_question_rejects_invalid_state(state: str) -> None:
    assert isinstance(Question.create("probability", "risk", state), Err)


@pytest.mark.parametrize(
    "operation,value_,distribution",
    [
        ("probability", -0.1, ()),
        ("probability", float("inf"), ()),
        ("probability", 0.5, (0.5, 0.5)),
        ("score", 2.0, (0.5, 0.5)),
        ("score", 0.2, (0.5, 0.5)),
        ("score", "bad", (0.5, 0.5)),
    ],
)
def test_invalid_probabilities_scores_and_usage(
    operation: str, value_: str | float, distribution: tuple[float, ...]
) -> None:
    q = value(
        Question.create(
            operation,
            "risk" if operation == "probability" else "signal",
            "{}",
            () if operation == "probability" else ("no", "yes"),
        )
    )
    assert isinstance(Answer.create(q, value_, 0.8, distribution, "test", Usage()), Err)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["jev", "llm"])
@pytest.mark.parametrize("mutation", ["unknown", "nonfinite", "usage", "operation"])
async def test_provider_contract_rejects_malformed_responses(
    name: str, mutation: str, recorded_probes: tuple[Probe, ...]
) -> None:
    tape = ReplayTransport(Path("evals/decision-replay"))
    q = recorded_probes[0].question

    def handler(request: httpx.Request) -> httpx.Response:
        raw = tape.handle(request).json()
        if mutation == "usage":
            raw["usage"]["input_tokens" if name == "jev" else "prompt_tokens"] = -1
        elif name == "jev":
            answer = raw["answers"]["decision"]
            if mutation == "unknown":
                answer["choice"] = "unlisted"
            elif mutation == "nonfinite":
                answer["confidence"] = "NaN"
            else:
                raw["answers"]["decision"] = {"type": "noul", "noul": 0.8}
        else:
            answer = json.loads(raw["choices"][0]["message"]["content"])
            answer[
                {"unknown": "value", "nonfinite": "confidence", "operation": "extra"}[mutation]
            ] = "bad"
            raw["choices"][0]["message"]["content"] = json.dumps(answer)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = (
            JevDecisionModel(client, settings(name, False))
            if name == "jev"
            else LlmDecisionModel(HttpLanguageModel(client, settings(name, False)))
        )
        with pytest.raises(ValueError):
            await model.choose(q)


@pytest.mark.asyncio
async def test_missing_replay_and_budget_do_not_call_live_providers(tmp_path: Path) -> None:
    report = await benchmark(Path("evals"), tmp_path, max_tokens=1)
    assert not report.passed
    assert report.adapters["jev"]["priority"]["errors"] == 99
    async with httpx.AsyncClient() as client:
        for configuration in (
            "[]",
            '{"unknown":"local"}',
            '{"guard":"other"}',
            '{"risk":"jev"}',
            '{"guard":"jev"}',
        ):
            with pytest.raises(ValueError):
                configured_decisions({"CASEFILE_DECISIONS": configuration}, client)
        bridge = configured_decisions({"CASEFILE_DECISIONS": '{"guard":"local"}'}, client)
        assert set(bridge.routes) == {"risk", "guard"}


@pytest.mark.asyncio
async def test_empty_environment_keeps_demo_decisions() -> None:
    async with httpx.AsyncClient() as client:
        bridge = configured_decisions(
            {"CASEFILE_DECISIONS": "", "CASEFILE_JEV_INPUT_COST_PER_MILLION": ""}, client
        )
        assert set(bridge.routes) == {"risk"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key, entry, message",
    [
        ("CASEFILE_JEV_API_KEY", "", "CASEFILE_JEV_API_KEY"),
        ("CASEFILE_JEV_API_KEY", "   ", "CASEFILE_JEV_API_KEY"),
        ("CASEFILE_JEV_INPUT_COST_PER_MILLION", "", "token rates"),
        ("CASEFILE_JEV_OUTPUT_COST_PER_MILLION", "invalid", "token rates"),
    ],
)
async def test_enabled_jev_rejects_unfilled_environment(key: str, entry: str, message: str) -> None:
    env = {
        "CASEFILE_DECISIONS": '{"priority":"jev"}',
        "CASEFILE_JEV_URL": "https://api.typesafe.ai/v1/systemone",
        "CASEFILE_JEV_MODEL": "jev-latest",
        "CASEFILE_JEV_API_KEY": "test-only-key",
        "CASEFILE_JEV_INPUT_COST_PER_MILLION": "0.1",
        "CASEFILE_JEV_OUTPUT_COST_PER_MILLION": "0",
        key: entry,
    }
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match=message):
            configured_decisions(env, client)


@pytest.mark.asyncio
async def test_jev_environment_routes_authenticated_requests(
    recorded_probes: tuple[Probe, ...],
) -> None:
    tape = ReplayTransport(Path("evals/decision-replay"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-only-key"
        response = tape.handle(request).json()
        response["usage"] = {"input_tokens": 100, "output_tokens": 20}
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bridge = configured_decisions(
            {
                "CASEFILE_DECISIONS": '{"priority":"jev"}',
                "CASEFILE_JEV_URL": settings("jev", False).url,
                "CASEFILE_JEV_MODEL": settings("jev", False).model,
                "CASEFILE_JEV_API_KEY": "test-only-key",
                "CASEFILE_JEV_INPUT_COST_PER_MILLION": "0.1",
                "CASEFILE_JEV_OUTPUT_COST_PER_MILLION": "0.2",
            },
            client,
        )
        assert set(bridge.routes) == {"risk", "priority"}
        answer = await bridge.routes["priority"].choose(recorded_probes[0].question)
        assert answer.usage.tokens > 0
        assert answer.usage.cost > 0
        assert "test-only-key" not in repr(bridge)
