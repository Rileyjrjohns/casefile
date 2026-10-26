"""Provider recordings carry the model they were made with, so replays match their keys."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from casefile.bootstrap.decision_benchmark import benchmark, settings, write_synthetic_fixtures
from casefile.bootstrap.decision_recording import Recording, canonical
from tests.decision_support import dataset

RECORDED_AT = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)


def synthetic(fixtures: Path, name: str) -> Recording:
    return Recording.model_validate_json((fixtures / f"{name}.json").read_text())


def pinned_recording(
    root: Path, model: str, fixtures: Path, *, keys_for: str | None = None
) -> None:
    """A provider recording whose request keys were built with `keys_for` (default `model`).

    Its answers are those of the synthetic Jev fixture in `fixtures`.
    """
    responses = {}
    for key, response in synthetic(fixtures, "jev").responses.items():
        body = json.loads(key)
        body["model"] = keys_for or model
        responses[canonical(body)] = response
    (root / "decision-replay").mkdir(exist_ok=True)
    (root / "decision-replay" / "jev.json").write_text(
        Recording(
            schema_version=2,
            provenance="provider-recording",
            model=model,
            recorded_at=RECORDED_AT,
            responses=responses,
        ).model_dump_json()
    )


@pytest.mark.parametrize("name", ["jev", "llm"])
def test_synthetic_fixtures_still_load_and_keep_their_synthetic_request_model(
    name: str, synthetic_replay: Path
) -> None:
    recording = synthetic(synthetic_replay, name)
    assert recording.schema_version == 1
    assert recording.provenance == "synthetic-protocol-fixture"
    assert recording.model is None
    assert recording.recorded_at is None
    assert settings(name, False).model == f"{name}-synthetic-protocol-v1"
    assert settings(name, False, recording).model == f"{name}-synthetic-protocol-v1"


@pytest.mark.asyncio
async def test_synthetic_fixtures_regenerate_byte_for_byte(
    tmp_path: Path, synthetic_replay: Path
) -> None:
    root = dataset(tmp_path)
    await write_synthetic_fixtures(root)
    for name in ("jev", "llm"):
        assert (root / "decision-replay" / f"{name}.json").read_bytes() == (
            synthetic_replay / f"{name}.json"
        ).read_bytes()


def test_the_committed_llm_fixture_is_what_the_generator_writes(synthetic_replay: Path) -> None:
    committed = Path("evals/decision-replay/llm.json")
    assert committed.read_bytes() == (synthetic_replay / "llm.json").read_bytes()


def test_a_provider_recording_names_its_request_model_and_date() -> None:
    recording = Recording.model_validate(
        {
            "schema_version": 2,
            "provenance": "provider-recording",
            "model": "jev-latest",
            "recorded_at": "2026-10-26T09:30:00Z",
            "responses": {},
        }
    )
    assert recording.recorded_at == RECORDED_AT
    assert settings("jev", False, recording).model == "jev-latest"
    assert settings("jev", False, recording).api_key == ""


@pytest.mark.parametrize(
    "fields",
    [
        {"schema_version": 1},
        {"model": None},
        {"model": "  "},
        {"recorded_at": None},
        {"recorded_at": "2026-10-26T09:30:00"},
        {"provenance": "synthetic-protocol-fixture"},
        {"url": "https://provider.example/v1/systemone"},
        {"headers": {"Authorization": "Bearer secret"}},
    ],
)
def test_a_provider_recording_with_missing_or_foreign_fields_is_rejected(
    fields: dict[str, object],
) -> None:
    document: dict[str, object] = {
        "schema_version": 2,
        "provenance": "provider-recording",
        "model": "jev-latest",
        "recorded_at": "2026-10-26T09:30:00Z",
        "responses": {},
    }
    with pytest.raises(ValidationError):
        Recording.model_validate({**document, **fields})


@pytest.mark.parametrize(
    "fields", [{"schema_version": 2}, {"model": "jev-latest"}, {"recorded_at": "2026-10-26T09:30Z"}]
)
def test_a_synthetic_fixture_cannot_claim_a_provider_identity(fields: dict[str, object]) -> None:
    document: dict[str, object] = {
        "schema_version": 1,
        "provenance": "synthetic-protocol-fixture",
        "responses": {},
    }
    with pytest.raises(ValidationError):
        Recording.model_validate({**document, **fields})


@pytest.mark.asyncio
async def test_replay_sends_the_model_the_recording_was_made_with(
    tmp_path: Path, synthetic_replay: Path
) -> None:
    root = dataset(tmp_path)
    pinned_recording(root, "jev-pinned-9", synthetic_replay)
    report = await benchmark(root, tmp_path / "out", providers=("jev",))
    assert report.passed
    assert report.provenance == {"jev": "provider-recording"}
    rows = json.loads((tmp_path / "out/decision-observations.json").read_text())["jev"]
    assert rows and all(row["error"] is None for row in rows)


@pytest.mark.asyncio
async def test_the_report_dates_a_replayed_recording_and_leaves_synthetic_fixtures_alone(
    tmp_path: Path, synthetic_replay: Path
) -> None:
    root = dataset(tmp_path, "llm", fixtures=synthetic_replay)
    pinned_recording(root, "jev-pinned-9", synthetic_replay)
    await benchmark(root, tmp_path / "out", providers=("jev", "llm"))
    text = (tmp_path / "out/decisions.md").read_text()
    assert "jev: requested model `jev-pinned-9`, recorded 2026-10-26" in text
    assert "genuine provider answers" in text
    assert "llm: requested model" not in text
    other = tmp_path / "other"
    other.mkdir()
    await benchmark(dataset(other, "jev", "llm", fixtures=synthetic_replay), tmp_path / "plain")
    assert "genuine provider answers" not in (tmp_path / "plain/decisions.md").read_text()


@pytest.mark.asyncio
async def test_replay_does_not_match_keys_built_for_another_model(
    tmp_path: Path, synthetic_replay: Path
) -> None:
    root = dataset(tmp_path)
    pinned_recording(root, "jev-pinned-9", synthetic_replay, keys_for="jev-pinned-8")
    report = await benchmark(root, tmp_path / "out", providers=("jev",))
    assert not report.passed
    assert report.adapters["jev"]["priority"]["errors"] == 99
