"""A live decision run can keep genuine provider answers for offline replay, and nothing else."""

import gzip
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from casefile.bootstrap import decision_benchmark as module
from casefile.bootstrap.decision_benchmark import ReplayTransport, benchmark
from casefile.bootstrap.decision_recording import Recording, RecordingTransport, canonical
from tests.test_decision_provider_selection import dataset
from tests.test_decision_recording import RECORDED_AT

API_KEY = "sk-test-do-not-store-7c1f"
MODEL = "jev-latest"
URL = "https://provider.test/v1/systemone"
SYNTHETIC_MODEL = "jev-synthetic-protocol-v1"


class Provider:
    """A stand-in for the live Jev API that answers the way the synthetic fixtures do."""

    def __init__(
        self, *, fail_on: int | None = None, input_tokens: int = 100, echo_key: bool = False
    ) -> None:
        self.tape = ReplayTransport(Path("evals/decision-replay"), ("jev",))
        self.fail_on = fail_on
        self.input_tokens = input_tokens
        self.echo_key = echo_key
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        if self.fail_on == len(self.requests):
            return httpx.Response(500, json={"error": "unavailable"})
        body = json.loads(request.content)
        assert body["model"] == MODEL
        body["model"] = SYNTHETIC_MODEL
        answer: dict[str, JsonValue] = dict(self.tape.recordings["jev"].responses[canonical(body)])
        answer["model"] = "jev-1.14.0"
        answer["usage"] = {"input_tokens": self.input_tokens, "output_tokens": 1}
        if self.echo_key:
            answer["debug"] = {"authorization": f"Bearer {API_KEY}"}
        return httpx.Response(200, json=answer)


def connect(monkeypatch: pytest.MonkeyPatch, provider: Provider) -> None:
    for key in tuple(os.environ):
        if key.startswith(("CASEFILE_JEV_", "CASEFILE_LLM_")):
            monkeypatch.delenv(key)
    for suffix, value in {
        "URL": URL,
        "MODEL": MODEL,
        "API_KEY": API_KEY,
        "INPUT_COST_PER_MILLION": "0.042",
        "OUTPUT_COST_PER_MILLION": "0",
    }.items():
        monkeypatch.setenv(f"CASEFILE_JEV_{suffix}", value)
    monkeypatch.setattr(module, "provider_transport", lambda: httpx.MockTransport(provider.handle))


def with_replay_copies(root: Path, *names: str) -> dict[str, bytes]:
    """Plain copies of synthetic fixtures, so a test can see whether a run touched them."""
    (root / "decision-replay").mkdir(exist_ok=True)
    for name in names:
        shutil.copyfile(
            Path(f"evals/decision-replay/{name}.json"), root / "decision-replay" / f"{name}.json"
        )
    return {name: (root / "decision-replay" / f"{name}.json").read_bytes() for name in names}


def answers(output: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = json.loads((output / "decision-observations.json").read_text())[
        "jev"
    ]
    return [{k: v for k, v in row.items() if k not in {"latency_ms", "cost"}} for row in rows]


@pytest.mark.asyncio
async def test_a_recorded_live_run_replays_to_the_same_answers_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = Provider()
    connect(monkeypatch, provider)
    root = dataset(tmp_path)
    untouched = with_replay_copies(root, "llm")
    live = await benchmark(
        root, tmp_path / "live", live=True, providers=("jev",), record_at=RECORDED_AT
    )
    assert live.passed
    assert live.provenance == {"jev": "live provider"}
    assert live.recordings == {"jev": "written"}
    calls = len(provider.requests)
    assert calls > 2000

    path = root / "decision-replay" / "jev.json"
    recording = Recording.model_validate_json(path.read_text())
    assert recording.provenance == "provider-recording"
    assert recording.schema_version == 2
    assert recording.model == MODEL
    assert recording.recorded_at == RECORDED_AT
    assert 0 < len(recording.responses) <= calls
    assert (root / "decision-replay" / "llm.json").read_bytes() == untouched["llm"]
    assert sorted(p.name for p in path.parent.iterdir()) == ["jev.json", "llm.json"]

    offline = await benchmark(root, tmp_path / "offline", providers=("jev",))
    assert len(provider.requests) == calls
    assert offline.passed
    assert offline.provenance == {"jev": "provider-recording"}
    assert offline.recordings == {}
    assert offline.models == live.models == {"jev": ("jev-1.14.0", "singleton-route")}
    assert answers(tmp_path / "offline") == answers(tmp_path / "live")
    assert offline.adapters["jev"].keys() == live.adapters["jev"].keys()
    assert (
        "jev: requested model `jev-latest`, recorded 2026-10-26T09:30:00Z"
        in (tmp_path / "offline/decisions.md").read_text()
    )


@pytest.mark.asyncio
async def test_a_recording_holds_request_and_response_bodies_and_no_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(monkeypatch, Provider())
    root = dataset(tmp_path)
    await benchmark(root, tmp_path / "out", live=True, providers=("jev",), record_at=RECORDED_AT)
    raw = (root / "decision-replay" / "jev.json").read_bytes()
    for forbidden in (API_KEY, "Authorization", "Bearer", "provider.test", "api_key"):
        assert forbidden.encode() not in raw, forbidden
    document = json.loads(raw)
    assert set(document) == {"schema_version", "provenance", "model", "recorded_at", "responses"}
    for key, response in document["responses"].items():
        assert set(json.loads(key)) == {"model", "state", "questions"}
        assert set(response) == {"model", "answers", "usage"}


@pytest.mark.asyncio
async def test_a_response_that_echoes_the_credential_is_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(monkeypatch, Provider(echo_key=True))
    root = dataset(tmp_path)
    report = await benchmark(
        root, tmp_path / "out", live=True, providers=("jev",), record_at=RECORDED_AT
    )
    assert not report.passed
    assert report.recordings == {"jev": "not written: a provider response contains the API key"}
    assert not (root / "decision-replay").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http-error", "budget", "malformed"])
async def test_a_failed_or_stopped_run_writes_no_recording_and_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    provider = Provider(
        fail_on=7 if failure == "http-error" else None,
        # Four thousand tokens a call exhaust a 15,000 token budget after a few answers.
        input_tokens={"budget": 4_000, "malformed": -1}.get(failure, 100),
    )
    connect(monkeypatch, provider)
    root = dataset(tmp_path)
    untouched = with_replay_copies(root, "jev")
    report = await benchmark(
        root,
        tmp_path / "out",
        live=True,
        providers=("jev",),
        record_at=RECORDED_AT,
        max_tokens=15_000 if failure == "budget" else 2_500_000,
    )
    assert not report.passed
    assert report.recordings["jev"].startswith("not written: ")
    assert 0 < len(provider.requests) < (10 if failure == "budget" else 10_000)
    assert (root / "decision-replay" / "jev.json").read_bytes() == untouched["jev"]
    assert sorted(p.name for p in (root / "decision-replay").iterdir()) == ["jev.json"]
    # The partial run did not become something the offline benchmark replays.
    offline = await benchmark(root, tmp_path / "offline", providers=("jev",))
    assert offline.provenance == {"jev": "synthetic-protocol-fixture"}


@pytest.mark.asyncio
async def test_recording_over_a_linked_fixture_replaces_the_link_not_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(monkeypatch, Provider())
    root = dataset(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    target = shared / "jev.json"
    shutil.copyfile("evals/decision-replay/jev.json", target)
    before = target.read_bytes()
    (root / "decision-replay").mkdir()
    (root / "decision-replay" / "jev.json").symlink_to(target)
    await benchmark(root, tmp_path / "out", live=True, providers=("jev",), record_at=RECORDED_AT)
    assert target.read_bytes() == before
    assert not (root / "decision-replay" / "jev.json").is_symlink()
    assert "provider-recording" in (root / "decision-replay" / "jev.json").read_text()


@pytest.mark.asyncio
async def test_recording_needs_a_live_run_with_a_remote_provider_and_an_aware_instant(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="live"):
        await benchmark(Path("evals"), tmp_path, providers=("jev",), record_at=RECORDED_AT)
    with pytest.raises(ValueError, match="remote provider"):
        await benchmark(
            Path("evals"), tmp_path, live=True, providers=("local",), record_at=RECORDED_AT
        )
    with pytest.raises(ValueError, match="timezone"):
        await benchmark(
            Path("evals"),
            tmp_path,
            live=True,
            providers=("jev",),
            record_at=datetime(2026, 10, 26, 9, 30),
        )


@pytest.mark.asyncio
async def test_a_recording_must_fit_the_token_budget_the_offline_benchmark_applies(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="2,500,000 token"):
        await benchmark(
            Path("evals"),
            tmp_path,
            live=True,
            providers=("jev",),
            record_at=RECORDED_AT,
            max_tokens=2_500_001,
        )


def command(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["casefile-decisions", *arguments])
    with pytest.raises(SystemExit) as result:
        module.main()
    return int(result.value.code or 0)


def test_the_command_records_a_live_run_and_reports_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    connect(monkeypatch, Provider())
    root = dataset(tmp_path)
    before = datetime.now(UTC)
    code = command(
        monkeypatch,
        "--live",
        "--record",
        "--providers",
        "jev",
        "--dataset",
        str(root),
        "--output",
        str(tmp_path / "out"),
    )
    assert code == 0
    recording = Recording.model_validate_json((root / "decision-replay/jev.json").read_text())
    assert recording.recorded_at is not None
    assert before <= recording.recorded_at <= datetime.now(UTC)
    assert (
        f"Recording jev: written to {root / 'decision-replay/jev.json'}" in capsys.readouterr().out
    )


@pytest.mark.parametrize("failure", ["http-error", "budget"])
def test_the_command_exits_non_zero_and_names_the_provider_that_was_not_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    connect(monkeypatch, Provider(fail_on=3 if failure == "http-error" else None))
    root = dataset(tmp_path)
    budget = ("--max-tokens", "1") if failure == "budget" else ()
    code = command(
        monkeypatch,
        "--live",
        "--record",
        "--providers",
        "jev",
        "--dataset",
        str(root),
        "--output",
        str(tmp_path / "out"),
        *budget,
    )
    assert code != 0
    assert "Recording jev: not written: " in capsys.readouterr().err
    assert not (root / "decision-replay").exists()


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["--record"], "--record requires --live"),
        (["--record", "--providers", "jev"], "--record requires --live"),
        (["--live", "--record", "--providers", "local"], "remote provider"),
    ],
)
def test_the_command_rejects_record_without_live_or_without_a_remote_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    root = dataset(tmp_path)
    assert command(monkeypatch, *arguments, "--dataset", str(root)) == 2
    assert message in capsys.readouterr().err
    assert not (root / "decision-replay").exists()


@pytest.mark.asyncio
async def test_the_transport_keeps_successful_json_answers_by_request_body_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n = json.loads(request.content)["n"]
        if n == 3:
            return httpx.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(b'{"answer":9}')
            )
        if n == 4:
            return httpx.Response(404, json={"answer": n})
        if n == 5:
            return httpx.Response(200, text="not json")
        if n == 6:
            return httpx.Response(200, json=[n])
        return httpx.Response(200, json={"answer": len(seen)})

    transport = RecordingTransport(
        httpx.MockTransport(handler),
        {"jev": "https://provider.test/ok", "llm": "https://other.test/chat"},
    )

    async def send(url: str, n: int, *, read: bool = True) -> httpx.Response:
        async with (
            httpx.AsyncClient(transport=transport) as client,
            client.stream(
                "POST", url, json={"n": n, "b": 1}, headers={"Authorization": "Bearer secret"}
            ) as response,
        ):
            if read:
                await response.aread()
            return response

    assert (await send("https://provider.test/ok", 1)).json() == {"answer": 1}
    assert (await send("https://provider.test/ok", 1)).json() == {"answer": 2}
    assert (await send("https://other.test/chat", 2)).json() == {"answer": 3}
    assert (await send("https://provider.test/ok", 3)).json() == {"answer": 9}
    assert (await send("https://provider.test/ok", 4)).status_code == 404
    assert (await send("https://provider.test/ok", 5)).text == "not json"
    assert (await send("https://provider.test/ok", 6)).json() == [6]
    assert (await send("https://elsewhere.test/ok", 7)).json() == {"answer": 8}
    await send("https://provider.test/ok", 8, read=False)

    assert len(seen) == 9
    assert transport.responses == {
        "jev": {
            canonical({"n": 1, "b": 1}): {"answer": 1},
            canonical({"n": 3, "b": 1}): {"answer": 9},
        },
        "llm": {canonical({"n": 2, "b": 1}): {"answer": 3}},
    }
