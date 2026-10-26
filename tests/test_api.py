from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest

from casefile.bootstrap.api import create_app
from casefile.bootstrap.runtime import create_memory_runtime


@pytest.mark.asyncio
async def test_api_full_investigation_and_confirmed_review() -> None:
    runtime = await create_memory_runtime(seed=False)
    api = create_app(runtime, background_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/cases",
            json={
                "alert_id": "api-alert",
                "subject_id": "demo-customer-002",
                "kind": "unusual_transaction",
                "description": "Fictional API investigation",
            },
        )
        assert response.status_code == 201, response.text
        case_id = response.json()["id"]
        assert len((await client.get("/cases")).json()["cases"]) == 1
        started = await client.post(f"/cases/{case_id}/runs", json={})
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        assert (await client.post(f"/cases/{case_id}/runs", json={})).status_code == 409
        assert await runtime.worker.run_once()
        detail = (await client.get(f"/runs/{run_id}")).json()
        assert detail["run"]["state"] == "AWAITING_REVIEW"
        assert detail["events"]
        conclusions = detail["run"]["analysis"]["conclusions"]
        source = conclusions[0]["sources"][0]
        assert (
            await client.get(f"/runs/{run_id}/sources/{source['tool']}/{source['record_id']}")
        ).status_code == 200
        assert (
            await client.get(f"/runs/{run_id}/sources/list_transactions/invented")
        ).status_code == 404
        review = {
            "expected_revision": detail["revision"],
            "decision": "escalate",
            "confirmed": False,
            "conclusions": [{"conclusion_id": c["id"], "action": "validate"} for c in conclusions],
        }
        assert (await client.post(f"/runs/{run_id}/review", json=review)).status_code == 422
        review["confirmed"] = True
        review["expected_revision"] = 1
        assert (await client.post(f"/runs/{run_id}/review", json=review)).status_code == 409
        review["expected_revision"] = detail["revision"]
        assert (await client.post(f"/runs/{run_id}/review", json=review)).status_code == 200
        closed = (await client.get(f"/runs/{run_id}")).json()
        assert closed["run"]["state"] == "CLOSED"
        assert closed["run"]["human_decision"]["reviewer_id"] == str(runtime.reviewer)
        assert closed["review"]["decision"] == "escalate"
        assert closed["review"]["conclusions"][0]["action"] == "validate"
        assert (await client.get(f"/cases/{case_id}")).status_code == 200
        assert len((await client.get(f"/cases/{case_id}/runs")).json()["runs"]) == 1
        restarted = await client.post(f"/cases/{case_id}/runs", json={"id": str(UUID(int=1))})
        assert restarted.status_code == 202
        history = (await client.get(f"/cases/{case_id}/runs")).json()["runs"]
        assert [item["id"] for item in history] == [str(UUID(int=1)), run_id]
        assert history[1]["state"] == "CLOSED"
        assert (await client.get(f"/runs/{run_id}")).json()["review"] == closed["review"]
        assert (await client.post(f"/cases/{case_id}/runs", json={})).status_code == 409


@pytest.mark.asyncio
async def test_api_validation_not_found_and_origin_protection() -> None:
    api = create_app(await create_memory_runtime(seed=False), background_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        assert (await client.get(f"/cases/{UUID(int=999)}")).status_code == 404
        assert (await client.get(f"/runs/{UUID(int=999)}")).status_code == 404
        assert (await client.get("/cases?limit=1000")).status_code == 422
        assert (await client.post("/cases", json={})).status_code == 422
        assert (
            await client.post("/cases", json={}, headers={"origin": "https://hostile.example"})
        ).status_code == 403
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_validation_errors_report_locations_without_echoing_input() -> None:
    api = create_app(await create_memory_runtime(seed=False), background_worker=False)
    secret = "SECRET-" + "x" * 200_000
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        response = await client.post(
            "/cases",
            json={
                "alert_id": secret,
                "subject_id": 123,
                "kind": secret,
                "description": "Fictional",
                "extra": secret,
            },
        )
        malformed = await client.post(
            "/cases",
            content=b'{"alert_id": "SECRET-raw", ',
            headers={"content-type": "application/json"},
        )
        bad_query = await client.get("/cases?limit=SECRET-limit")
    for rejected in (response, malformed, bad_query):
        assert rejected.status_code == 422
        assert "SECRET" not in rejected.text
        assert len(rejected.content) < 4_000
    body = response.json()
    assert body["error"] == "validation_error"
    assert body["message"]
    assert {"loc": ["body", "subject_id"], "type": "string_type"} in body["detail"]
    assert all(set(item) == {"loc", "type"} for item in body["detail"])


@pytest.mark.asyncio
async def test_validation_errors_bound_client_chosen_key_names_and_item_count() -> None:
    api = create_app(await create_memory_runtime(seed=False), background_worker=False)
    huge_key = "SECRETKEY" + "x" * 100_000
    many_keys = {f"unknown-{index}": index for index in range(500)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        long_key = await client.post(
            "/cases",
            json={
                "alert_id": "a",
                "subject_id": "s",
                "kind": "unusual_transaction",
                "description": "Fictional",
                huge_key: 1,
            },
        )
        crowded = await client.post("/cases", json=many_keys)
    for rejected in (long_key, crowded):
        assert rejected.status_code == 422
        assert "SECRETKEY" not in rejected.text
        assert len(rejected.content) < 4_000
        assert all(
            len(str(part)) <= 64 for item in rejected.json()["detail"] for part in item["loc"]
        )


@pytest.mark.asyncio
async def test_oversized_request_bodies_are_refused_with_413() -> None:
    api = create_app(await create_memory_runtime(seed=False), background_worker=False)
    oversized = b'{"description": "' + b"x" * (1024 * 1024) + b'"}'
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        declared = await client.post(
            "/cases", content=oversized, headers={"content-type": "application/json"}
        )

        async def chunks() -> AsyncIterator[bytes]:
            for start in range(0, len(oversized), 64 * 1024):
                yield oversized[start : start + 64 * 1024]

        streamed = await client.post(
            "/cases", content=chunks(), headers={"content-type": "application/json"}
        )
        small = await client.post("/cases", json={})
    for refused in (declared, streamed):
        assert refused.status_code == 413
        assert refused.json()["error"] == "payload_too_large"
        assert "xxxx" not in refused.text
    assert small.status_code == 422
