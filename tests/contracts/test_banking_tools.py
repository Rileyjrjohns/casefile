from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from casefile.bootstrap.tools import connect_demo_tools
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import ToolSeed, load_seed
from casefile.features.investigation.application.agent_types import EvidenceBatch
from casefile.features.investigation.application.tools import InvestigationTools, ToolName
from casefile.shared.result import Err, Ok

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["memory", "mcp"], scope="module")
async def banking_tools(request: pytest.FixtureRequest) -> AsyncIterator[InvestigationTools]:
    if request.param == "memory":
        yield MemoryInvestigationTools(load_seed(banking=True))
    else:
        async with connect_demo_tools(banking=True) as tools:
            yield tools


@pytest.mark.parametrize("number,count", [(101, 4), (102, 9), (103, 3)])
async def test_documents_are_isolated_immutable_and_replayable(
    banking_tools: InvestigationTools, number: int, count: int
) -> None:
    customer = f"demo-customer-{number}"
    result = await banking_tools.list_case_documents(customer)
    assert isinstance(result, Ok) and len(result.value) == count
    assert all(record.customer_id == customer for record in result.value)
    assert await banking_tools.list_case_documents(customer) == result
    record = result.value[0]
    with pytest.raises(FrozenInstanceError):
        record.fields[0].__setattr__("value", "tampered")
    codec = AgentJsonCodec()
    batch = EvidenceBatch(ToolName.DOCUMENTS, customer, result.value)
    assert codec.evidence(codec.evidence_json(batch)) == batch


async def test_document_queries_enforce_the_fictional_customer_boundary(
    banking_tools: InvestigationTools,
) -> None:
    assert isinstance(await banking_tools.list_case_documents("not-a-demo-customer"), Err)
    assert await banking_tools.list_case_documents("demo-customer-999") == Ok(())


def test_duplicate_document_fields_are_rejected() -> None:
    raw = load_seed(banking=True).model_dump(mode="json")
    raw["documents"][0]["fields"].append(raw["documents"][0]["fields"][0])
    with pytest.raises(ValidationError, match="unique names"):
        ToolSeed.model_validate(raw)
