"""In-memory implementations of the read-only evidence ports."""

from collections.abc import Callable

from casefile.features.investigation.adapters.outbound.tools.seed import ToolSeed
from casefile.features.investigation.adapters.outbound.tools.wire import CustomerRecordDocument
from casefile.features.investigation.application.tools import (
    AlertRecord,
    CaseDocument,
    InvestigatorNote,
    KycProfile,
    SanctionEntry,
    ToolResult,
    Transaction,
    customer_query,
    sanction_query,
)
from casefile.shared.result import Err, Ok


def _lookup[D: CustomerRecordDocument, T](
    customer_id: str,
    records: tuple[D, ...],
    convert: Callable[[D], T],
) -> ToolResult[T]:
    query = customer_query(customer_id)
    if isinstance(query, Err):
        return query
    return Ok(tuple(convert(record) for record in records if record.customer_id == query.value))


class MemoryInvestigationTools:
    def __init__(self, seed: ToolSeed) -> None:
        self.seed = seed

    async def list_transactions(self, customer_id: str) -> ToolResult[Transaction]:
        return _lookup(customer_id, self.seed.transactions, lambda record: record.to_record())

    async def get_kyc_profile(self, customer_id: str) -> ToolResult[KycProfile]:
        return _lookup(customer_id, self.seed.kyc, lambda record: record.to_record())

    async def list_alerts(self, customer_id: str) -> ToolResult[AlertRecord]:
        return _lookup(customer_id, self.seed.alerts, lambda record: record.to_record())

    async def search_sanctions(self, name: str) -> ToolResult[SanctionEntry]:
        query = sanction_query(name)
        if isinstance(query, Err):
            return query
        return Ok(
            tuple(
                record.to_record()
                for record in self.seed.sanctions
                if any(
                    " ".join(alias.split()).casefold() == query.value
                    for alias in (record.name, *record.aliases)
                )
            )
        )

    async def list_investigator_notes(self, customer_id: str) -> ToolResult[InvestigatorNote]:
        return _lookup(customer_id, self.seed.notes, lambda record: record.to_record())

    async def list_case_documents(self, customer_id: str) -> ToolResult[CaseDocument]:
        return _lookup(customer_id, self.seed.documents, lambda record: record.to_record())
