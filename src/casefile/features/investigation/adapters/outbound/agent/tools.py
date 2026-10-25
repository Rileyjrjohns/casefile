"""Bridge typed evidence ports and replay recorded calls, including failures."""

from casefile.features.investigation.adapters.outbound.agent.replay import ReplayTape
from casefile.features.investigation.application.agent_types import (
    AgentCodec,
    EvidenceBatch,
    TraceEvent,
    tool_input,
)
from casefile.features.investigation.application.tools import (
    InvalidToolQuery,
    InvestigationTools,
    ToolName,
)
from casefile.shared.result import Err, Ok, Result


class TypedEvidenceGateway:
    def __init__(self, tools: InvestigationTools) -> None:
        self.tools = tools

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        match tool:
            case ToolName.DOCUMENTS:
                documents = await self.tools.list_case_documents(query)
                return (
                    documents
                    if isinstance(documents, Err)
                    else Ok(EvidenceBatch(tool, query, documents.value))
                )
            case ToolName.TRANSACTIONS:
                transactions = await self.tools.list_transactions(query)
                return (
                    transactions
                    if isinstance(transactions, Err)
                    else Ok(EvidenceBatch(tool, query, transactions.value))
                )
            case ToolName.KYC:
                kyc = await self.tools.get_kyc_profile(query)
                return kyc if isinstance(kyc, Err) else Ok(EvidenceBatch(tool, query, kyc.value))
            case ToolName.ALERTS:
                alerts = await self.tools.list_alerts(query)
                return (
                    alerts
                    if isinstance(alerts, Err)
                    else Ok(EvidenceBatch(tool, query, alerts.value))
                )
            case ToolName.SANCTIONS:
                sanctions = await self.tools.search_sanctions(query)
                return (
                    sanctions
                    if isinstance(sanctions, Err)
                    else Ok(EvidenceBatch(tool, query, sanctions.value))
                )
            case ToolName.NOTES:
                notes = await self.tools.list_investigator_notes(query)
                return (
                    notes if isinstance(notes, Err) else Ok(EvidenceBatch(tool, query, notes.value))
                )


class ReplayEvidenceGateway:
    def __init__(self, events: tuple[TraceEvent, ...], codec: AgentCodec) -> None:
        self.tape = ReplayTape(events)
        self.codec = codec

    async def fetch(self, tool: ToolName, query: str) -> Result[EvidenceBatch, InvalidToolQuery]:
        expected = tool_input(tool, query)
        event = self.tape.consume(f"tool.{tool}", expected)
        if event.phase == "failed":
            return Err(InvalidToolQuery((event.error or "").removeprefix("query:")))
        if event.output_json is None:
            raise ValueError("Replay tool response is missing.")
        batch = self.codec.evidence(event.output_json)
        if batch.tool != tool or batch.query != query:
            raise ValueError("Replay evidence does not match the request.")
        return Ok(batch)
