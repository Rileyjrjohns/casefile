"""Richer fictional dossiers use sourced facts and retain the human decision boundary."""

from uuid import UUID

import httpx
import pytest

from casefile.bootstrap.api import create_app
from casefile.bootstrap.runtime import create_memory_runtime, seed_runtime
from casefile.features.investigation.adapters.inbound.documents import StartRunDocument
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.shared.ids import CaseId


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "number,recommendation,required_text",
    [
        (101, "escalate", "beneficiary account"),
        (102, "escalate", "1600.00"),
        (103, "close", "different dates of birth"),
    ],
)
async def test_banking_dossiers_produce_sourced_findings_without_final_decisions(
    number: int, recommendation: str, required_text: str
) -> None:
    runtime = await create_memory_runtime(seed=False, banking=True)
    await seed_runtime(runtime, banking=True)
    case_id = UUID(int=1000 + number)
    assert await runtime.app.cases.get(CaseId(case_id)) is not None
    assert not await runtime.app.runs.for_case(CaseId(case_id))
    api = create_app(runtime, background_worker=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://localhost"
    ) as client:
        started = await client.post(f"/cases/{case_id}/runs", json={})
        assert started.status_code == 202
        run_id = started.json()["id"]
        while await runtime.worker.run_once():
            pass
        detail = (await client.get(f"/runs/{run_id}")).json()
        run = detail["run"]
        assert run["state"] == "AWAITING_REVIEW"
        assert run["human_decision"] is None
        assert run["usage"]["tool_calls"] == 6
        assert run["analysis"]["recommendation"] == recommendation
        conclusions = run["analysis"]["conclusions"]
        assert any(required_text in item["statement"] for item in conclusions)
        assert any(
            "does not" in item["statement"] or "matches" in item["statement"]
            for item in conclusions
        )
        for conclusion in conclusions:
            for source in conclusion["sources"]:
                response = await client.get(
                    f"/runs/{run_id}/sources/{source['tool']}/{source['record_id']}"
                )
                assert response.status_code == 200
        assert any(
            source["tool"] == "list_case_documents"
            for item in conclusions
            for source in item["sources"]
        )
        await seed_runtime(runtime, banking=True)
        assert len(await runtime.app.runs.for_case(CaseId(case_id))) == 1


def test_banking_seed_is_additive_and_keeps_the_original_fixture() -> None:
    original = load_seed()
    expanded = load_seed(banking=True)
    assert len(original.kyc) == 3
    assert len(expanded.kyc) == 6
    assert expanded.kyc[:3] == original.kyc
    assert expanded.transactions[: len(original.transactions)] == original.transactions
    assert not original.documents
    assert len(expanded.documents) >= 15


@pytest.mark.asyncio
async def test_explicit_tool_budget_is_never_increased() -> None:
    runtime = await create_memory_runtime(banking=True)
    case_id = UUID(int=1101)
    created = await runtime.start_run(case_id, StartRunDocument(tool_calls=5))
    limits = created["limits"]
    assert isinstance(limits, dict) and limits["tool_calls"] == 5
