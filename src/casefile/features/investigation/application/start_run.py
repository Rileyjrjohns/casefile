from dataclasses import dataclass

from casefile.features.investigation.application.ports import CaseLookup, RunStore
from casefile.features.investigation.domain.budget import BudgetLimits
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Err, Result


@dataclass(frozen=True, slots=True)
class StartRunCommand:
    run_id: RunId
    case_id: CaseId
    limits: BudgetLimits


@dataclass(frozen=True, slots=True)
class StartRun:
    cases: CaseLookup
    runs: RunStore

    async def execute(self, command: StartRunCommand) -> Result[Run, InvestigationError]:
        if not await self.cases.exists(command.case_id):
            return Err(
                InvestigationError(InvestigationErrorCode.CASE_NOT_FOUND, "Case does not exist.")
            )
        return await self.runs.enqueue(Run.new(command.run_id, command.case_id, command.limits))
