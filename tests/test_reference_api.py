"""API acceptance: create, investigate, correct, preserve and export a regression candidate."""

from dataclasses import replace
from uuid import UUID

import httpx
import pytest

from casefile.bootstrap.api import create_app
from casefile.bootstrap.runtime import create_memory_runtime
from casefile.shared.ids import ReviewerId


@pytest.mark.asyncio
async def test_complete_review_to_reference_export_keeps_original_evidence() -> None:
    runtime = await create_memory_runtime(seed=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime, background_worker=False)),
        base_url="http://localhost",
    ) as client:
        created = await client.post(
            "/cases",
            json={
                "alert_id": "acceptance-alert",
                "subject_id": "demo-customer-002",
                "kind": "unusual_transaction",
                "description": "Fictional acceptance investigation",
            },
        )
        assert created.status_code == 201
        run = await client.post(f"/cases/{created.json()['id']}/runs", json={})
        run_id = run.json()["id"]
        assert await runtime.worker.run_once()
        detail = (await client.get(f"/runs/{run_id}")).json()
        conclusions = detail["run"]["analysis"]["conclusions"]
        first = conclusions[0]
        path = f"/runs/{run_id}/conclusions/{first['id']}/reference"
        assert (await client.post(path, json={"confirmed": True})).status_code == 409
        corrected = "The volume discrepancy requires supporting documents before resolution."
        response = await client.post(
            f"/runs/{run_id}/review",
            json={
                "expected_revision": detail["revision"],
                "confirmed": True,
                "decision": "request_information",
                "conclusions": [
                    {
                        "conclusion_id": c["id"],
                        "action": "correct" if i == 0 else "validate",
                        **(
                            {
                                "corrected_statement": corrected,
                                "reason": "Clarify the follow-up required.",
                            }
                            if i == 0
                            else {}
                        ),
                    }
                    for i, c in enumerate(conclusions)
                ],
            },
        )
        assert response.status_code == 200
        assert (await client.post(path, json={"confirmed": False})).status_code == 422
        promoted = await client.post(path, json={"confirmed": True})
        assert promoted.status_code == 200, promoted.text
        reference_id = promoted.json()["id"]
        assert (await client.post(path, json={"confirmed": True})).json() == promoted.json()
        after = (await client.get(f"/runs/{run_id}")).json()
        assert after["run"]["analysis"] == detail["run"]["analysis"]
        assert after["run"]["state"] == "CLOSED"
        assert after["references"] == {first["id"]: reference_id}
        exported = await client.get(f"/references/{reference_id}")
        assert exported.status_code == 200
        bundle = exported.json()
        assert bundle["status"] == "requires_curation"
        assert bundle["expected"]["recommendation"] == "request_information"
        assert bundle["expected"]["conclusions"][0]["statement"] == corrected
        assert (
            bundle["investigation"]["run"]["analysis"]["conclusions"][0]["statement"]
            == first["statement"]
        )
        assert bundle["investigation"]["events"] == detail["events"]
        invalid = f"/runs/{run_id}/conclusions/{conclusions[1]['id']}/reference"
        assert (await client.post(invalid, json={"confirmed": True})).status_code == 422
        assert (await client.get(f"/references/{reference_id}")).json() == bundle


@pytest.mark.asyncio
async def test_reference_routes_require_the_demo_curator_and_report_absence() -> None:
    runtime = await create_memory_runtime(seed=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime, background_worker=False)),
        base_url="http://localhost",
    ) as client:
        assert (await client.get(f"/references/{UUID(int=987)}")).status_code == 404
    forbidden = replace(runtime, reviewer=ReviewerId(UUID(int=987)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(forbidden, background_worker=False)),
        base_url="http://localhost",
    ) as client:
        assert (await client.get(f"/references/{UUID(int=987)}")).status_code == 403
