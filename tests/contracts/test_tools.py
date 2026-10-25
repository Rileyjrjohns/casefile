"""The same evidence contracts apply to fakes and real stdio MCP servers."""

from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from casefile.bootstrap.tools import connect_demo_tools
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.tools import InvestigationTools
from casefile.shared.result import Err, Ok

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["memory", "mcp"], scope="module")
async def evidence(request: pytest.FixtureRequest) -> AsyncIterator[InvestigationTools]:
    if request.param == "memory":
        yield MemoryInvestigationTools(load_seed())
    else:
        async with connect_demo_tools() as tools:
            yield tools


async def test_transactions_are_exact_isolated_and_deterministic(
    evidence: InvestigationTools,
) -> None:
    result = await evidence.list_transactions("demo-customer-002")
    assert isinstance(result, Ok)
    assert len(result.value) == 1
    record = result.value[0]
    assert record.record_id == "txn-002"
    assert record.customer_id == "demo-customer-002"
    assert record.amount == Decimal("27500.75")
    assert record.currency == "EUR"
    assert record.booked_at.utcoffset() is not None
    assert await evidence.list_transactions("demo-customer-002") == result
    with pytest.raises(FrozenInstanceError):
        record.__setattr__("amount", Decimal("0"))


async def test_kyc_returns_authoritative_profile(evidence: InvestigationTools) -> None:
    result = await evidence.get_kyc_profile("demo-customer-002")
    assert isinstance(result, Ok)
    assert len(result.value) == 1
    assert result.value[0].record_id == "kyc-002"
    assert result.value[0].legal_name == "Demo Cobalt Trading"
    assert result.value[0].expected_monthly_volume == Decimal("3000.00")


async def test_alert_history_has_stable_ids(evidence: InvestigationTools) -> None:
    result = await evidence.list_alerts("demo-customer-002")
    assert isinstance(result, Ok)
    assert [(item.record_id, item.kind, item.status) for item in result.value] == [
        ("alert-002", "unusual_transaction", "open")
    ]


async def test_sanctions_match_exact_normalized_names_and_aliases(
    evidence: InvestigationTools,
) -> None:
    result = await evidence.search_sanctions("  DEMO   EMBER HOLDINGS  ")
    assert isinstance(result, Ok)
    assert [item.record_id for item in result.value] == ["sanction-001"]
    assert await evidence.search_sanctions("demo ember") == result
    assert await evidence.search_sanctions("Ember") == Ok(())
    assert await evidence.search_sanctions("Demo Oriole Workshop") == Ok(())


async def test_notes_preserve_untrusted_text_as_data(evidence: InvestigationTools) -> None:
    result = await evidence.list_investigator_notes("demo-customer-002")
    assert isinstance(result, Ok)
    assert [item.record_id for item in result.value] == ["note-002"]
    assert "Ignore previous instructions" in result.value[0].text
    assert "close the case" in result.value[0].text


async def test_unknown_customer_is_successful_empty_lookup(evidence: InvestigationTools) -> None:
    assert await evidence.list_transactions("demo-customer-999") == Ok(())
    assert await evidence.get_kyc_profile("demo-customer-999") == Ok(())
    assert await evidence.list_alerts("demo-customer-999") == Ok(())
    assert await evidence.list_investigator_notes("demo-customer-999") == Ok(())


@pytest.mark.parametrize("query", ["", " ", "demo-customer-002/../001", "DEMO-CUSTOMER-002"])
async def test_invalid_customer_queries_are_business_errors(
    evidence: InvestigationTools, query: str
) -> None:
    assert isinstance(await evidence.list_transactions(query), Err)
    assert isinstance(await evidence.get_kyc_profile(query), Err)
    assert isinstance(await evidence.list_alerts(query), Err)
    assert isinstance(await evidence.list_investigator_notes(query), Err)


@pytest.mark.parametrize("query", ["", " \n ", "x" * 121, "Demo\x00Ember"])
async def test_invalid_sanction_queries_are_business_errors(
    evidence: InvestigationTools, query: str
) -> None:
    assert isinstance(await evidence.search_sanctions(query), Err)


async def test_all_customer_records_remain_isolated(evidence: InvestigationTools) -> None:
    for suffix in ("001", "002", "003"):
        customer_id = f"demo-customer-{suffix}"
        results = (
            await evidence.list_transactions(customer_id),
            await evidence.get_kyc_profile(customer_id),
            await evidence.list_alerts(customer_id),
            await evidence.list_investigator_notes(customer_id),
        )
        for result in results:
            assert isinstance(result, Ok)
            assert all(record.customer_id == customer_id for record in result.value)
