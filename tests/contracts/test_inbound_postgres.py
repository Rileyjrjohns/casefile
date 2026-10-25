from uuid import UUID

import httpx
import pytest

from casefile.bootstrap.server import combined_app, runtime_context
from tests.contracts.support import Backend

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_postgres_api_reviews_and_sources_survive_runtime_restart(
    pg_backend: Backend,
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASEFILE_DATABASE_URL", postgres_url)
    monkeypatch.setenv("CASEFILE_TOOLS", "memory")
    monkeypatch.setenv("CASEFILE_LANGUAGE_MODEL", "fake")
    async with runtime_context() as runtime:
        for _ in range(3):
            assert await runtime.worker.run_once()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=combined_app(runtime)), base_url="http://localhost"
        ) as client:
            assert len((await client.get("/cases?limit=2")).json()["cases"]) == 2
            pages = [
                (await client.get(f"/cases?limit=2&offset={offset}")).json()["cases"]
                for offset in (0, 2, 4, 6)
            ]
            assert [len(page) for page in pages] == [2, 2, 2, 0]
            assert len({case["id"] for page in pages for case in page}) == 6
            case_id, run_id = str(UUID(int=4001)), str(UUID(int=4002))
            created = await client.post(
                "/cases",
                json={
                    "id": case_id,
                    "alert_id": "acceptance-correction",
                    "subject_id": "demo-customer-002",
                    "kind": "unusual_transaction",
                    "description": "Fictional review-to-regression acceptance",
                },
            )
            assert created.status_code == 201
            started = await client.post(f"/cases/{case_id}/runs", json={"id": run_id})
            assert started.status_code == 202
            assert await runtime.worker.run_once()
            assert len((await client.get(f"/cases/{case_id}/runs")).json()["runs"]) == 1
            detail = (await client.get(f"/runs/{run_id}")).json()
            source = await client.get(f"/runs/{run_id}/sources/risk_model@risk-v1/txn-002")
            assert source.status_code == 200
            response = await client.post(
                f"/runs/{run_id}/review",
                json={
                    "expected_revision": detail["revision"],
                    "decision": "escalate",
                    "confirmed": True,
                    "conclusions": [
                        {
                            "conclusion_id": c["id"],
                            "action": "correct" if i == 0 else "validate",
                            **(
                                {
                                    "corrected_statement": "Supporting documents are required.",
                                    "reason": "Clarify the unresolved volume discrepancy.",
                                }
                                if i == 0
                                else {}
                            ),
                        }
                        for i, c in enumerate(detail["run"]["analysis"]["conclusions"])
                    ],
                },
            )
            assert response.status_code == 200, response.text
            conclusion_id = detail["run"]["analysis"]["conclusions"][0]["id"]
            promoted = await client.post(
                f"/runs/{run_id}/conclusions/{conclusion_id}/reference",
                json={"confirmed": True},
            )
            assert promoted.status_code == 200, promoted.text
            reference_id = UUID(promoted.json()["id"])
            bundle = (await client.get(f"/references/{reference_id}")).json()
            assert bundle["expected"]["conclusions"][0]["statement"] == (
                "Supporting documents are required."
            )
            assert bundle["investigation"]["run"]["analysis"] == detail["run"]["analysis"]
            saved = (await client.get(f"/runs/{run_id}")).json()
            banking = await client.post(f"/cases/{UUID(int=1101)}/runs", json={})
            assert banking.status_code == 202
            banking_run_id = UUID(banking.json()["id"])
            assert await runtime.worker.run_once()
            banking_detail = (await client.get(f"/runs/{banking_run_id}")).json()
            assert banking_detail["run"]["state"] == "AWAITING_REVIEW"
            document = await client.get(
                f"/runs/{banking_run_id}/sources/list_case_documents/doc-103"
            )
            assert document.status_code == 200
            assert document.json()["record"]["kind"] == "payment_instruction"
    async with runtime_context() as restarted:
        persisted = await restarted.get_run(UUID(run_id))
        assert isinstance(persisted["run"], dict) and persisted["run"]["state"] == "CLOSED"
        assert persisted == saved
        assert await restarted.reference_bundle(reference_id) == bundle
        assert await restarted.get_run(banking_run_id) == banking_detail
        assert (
            await restarted.source(banking_run_id, "list_case_documents", "doc-103")
            == document.json()
        )
        assert not await restarted.worker.run_once()
