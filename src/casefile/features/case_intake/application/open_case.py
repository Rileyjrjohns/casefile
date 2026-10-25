from dataclasses import dataclass

from casefile.features.case_intake.application.ports import CaseRepository
from casefile.features.case_intake.domain.case import AlertKind, Case
from casefile.features.case_intake.domain.errors import IntakeError
from casefile.shared.clock import Clock
from casefile.shared.ids import CaseId
from casefile.shared.result import Err, Result


@dataclass(frozen=True, slots=True)
class OpenCaseCommand:
    case_id: CaseId
    alert_id: str
    subject_id: str
    kind: AlertKind
    description: str


@dataclass(frozen=True, slots=True)
class OpenCase:
    repository: CaseRepository
    clock: Clock

    async def execute(self, command: OpenCaseCommand) -> Result[Case, IntakeError]:
        case = Case.create(
            command.case_id,
            command.alert_id,
            command.subject_id,
            command.kind,
            command.description,
            self.clock.now(),
        )
        if isinstance(case, Err):
            return case
        return await self.repository.add(case.value)
