"""Wire investigation execution to case context, persistence and external adapters."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from casefile.bootstrap.decisions import demo_decisions
from casefile.bootstrap.memory import MemoryApplication
from casefile.bootstrap.postgres import PostgresApplication
from casefile.features.case_intake.application.ports import CaseRepository
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.agent.tools import TypedEvidenceGateway
from casefile.features.investigation.application.agent import InvestigationAgent
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    LanguageModel,
    Telemetry,
)
from casefile.features.investigation.application.decisions import DecisionGateway
from casefile.features.investigation.application.tools import InvestigationTools
from casefile.features.investigation.application.worker import RunWorker
from casefile.shared.clock import Clock
from casefile.shared.ids import CaseId


@dataclass(frozen=True, slots=True)
class StoredInvestigationContext:
    cases: CaseRepository

    async def get(self, case_id: CaseId) -> CaseContext | None:
        case = await self.cases.get(case_id)
        return (
            CaseContext(case.subject_id, case.alert_id, case.kind.value, case.description)
            if case
            else None
        )


def build_agent_worker(
    app: MemoryApplication | PostgresApplication,
    tools: InvestigationTools,
    model: LanguageModel,
    clock: Clock,
    telemetry: Telemetry,
    *,
    decisions: DecisionGateway | None = None,
    risk_enabled: bool = False,
    alternative_models: Mapping[str, LanguageModel] | None = None,
) -> RunWorker:
    agent = InvestigationAgent(
        app.journal,
        StoredInvestigationContext(app.cases),
        TypedEvidenceGateway(tools),
        model,
        AgentJsonCodec(),
        clock,
        telemetry,
        decisions or demo_decisions(),
        risk_enabled,
        alternative_models or {},
    )
    return RunWorker(
        app.queue, agent, lease_for=timedelta(minutes=5), execution_timeout=timedelta(minutes=4)
    )
