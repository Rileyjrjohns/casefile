"""Provider selection never requires or contacts an unselected provider."""

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from casefile.bootstrap import decision_benchmark as module
from casefile.bootstrap.decision_benchmark import ReplayTransport, benchmark, settings


def dataset(tmp_path: Path, *providers: str) -> Path:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "golden").symlink_to(Path("evals/golden").resolve(), target_is_directory=True)
    (root / "replay").symlink_to(Path("evals/replay").resolve(), target_is_directory=True)
    if providers:
        (root / "decision-replay").mkdir()
        for name in providers:
            (root / "decision-replay" / f"{name}.json").symlink_to(
                Path(f"evals/decision-replay/{name}.json").resolve()
            )
    return root


@pytest.mark.asyncio
async def test_local_only_needs_no_remote_recordings_or_credentials(tmp_path: Path) -> None:
    report = await benchmark(dataset(tmp_path), tmp_path / "out", providers=("local",))
    assert report.passed
    assert set(report.adapters) == set(report.provenance) == {"local"}
    assert report.models["local"]


@pytest.mark.asyncio
async def test_offline_subset_needs_only_selected_recording(tmp_path: Path) -> None:
    report = await benchmark(dataset(tmp_path, "jev"), tmp_path / "out", providers=("jev",))
    assert report.passed
    assert set(report.adapters) == {"jev"}
    assert report.models["jev"] == ("jev-synthetic-protocol-v1",)
    observations = json.loads((tmp_path / "out/decision-observations.json").read_text())
    assert observations["jev"][0]["expected"] == "normal"
    assert observations["jev"][0]["value"] == "normal"
    assert observations["jev"][0]["model"] == "jev-synthetic-protocol-v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("providers", [(), ("jev", "jev"), ("unknown",)])
async def test_invalid_selection_fails_before_loading_or_calling(
    tmp_path: Path, providers: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="provider"):
        await benchmark(tmp_path / "absent", tmp_path / "out", providers=providers)


def live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith(("CASEFILE_JEV_", "CASEFILE_LLM_")):
            monkeypatch.delenv(key)
    replay = settings("jev", False)
    for suffix, value in {
        "URL": replay.url,
        "MODEL": replay.model,
        "API_KEY": "test-only-key",
        "INPUT_COST_PER_MILLION": "0.042",
        "OUTPUT_COST_PER_MILLION": "0",
    }.items():
        monkeypatch.setenv(f"CASEFILE_JEV_{suffix}", value)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [1, 2_500_000])
async def test_live_jev_subset_keeps_budget_and_observation_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_tokens: int
) -> None:
    live_environment(monkeypatch)
    tape = ReplayTransport(Path("evals/decision-replay"))
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-only-key"
        response = tape.handle(request).json()
        response["model"] = "jev-test-version"
        response["usage"] = {"input_tokens": 100, "output_tokens": 1}
        return httpx.Response(200, json=response)

    real_client = httpx.AsyncClient

    def client(
        *, trust_env: bool, transport: httpx.AsyncBaseTransport | None = None
    ) -> httpx.AsyncClient:
        assert transport is None
        return real_client(trust_env=trust_env, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(ValueError, match="llm"):
        await benchmark(Path("evals"), tmp_path / "invalid", live=True, providers=("jev", "llm"))
    assert not requests
    report = await benchmark(
        dataset(tmp_path),
        tmp_path / "out",
        live=True,
        providers=("jev",),
        max_tokens=max_tokens,
    )
    assert report.passed is (max_tokens > 1)
    assert set(report.adapters) == {"jev"}
    assert report.provenance == {"jev": "live provider"}
    assert report.models["jev"] == (("jev-test-version",) if requests else ())
    rows = json.loads((tmp_path / "out/decision-observations.json").read_text())["jev"]
    assert len(rows) == len(module.probes(Path("evals")))
    assert len(requests) == (len(rows) if max_tokens > 1 else 0)
    assert all(Decimal(row["cost"]) == Decimal("0.0000042") for row in rows if not row["error"])
    assert "test-only-key" not in (tmp_path / "out/decision-observations.json").read_text()


@pytest.mark.parametrize("key", ["API_KEY", "INPUT_COST_PER_MILLION"])
def test_selected_live_provider_rejects_empty_credentials_or_rates(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    live_environment(monkeypatch)
    monkeypatch.setenv(f"CASEFILE_JEV_{key}", "")
    with pytest.raises(ValueError):
        settings("jev", True)


def test_cli_accepts_an_explicit_subset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["casefile-decisions", "--providers", "local", "--output", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 0
    report = json.loads((tmp_path / "decisions.json").read_text())
    assert list(report["adapters"]) == ["local"]
