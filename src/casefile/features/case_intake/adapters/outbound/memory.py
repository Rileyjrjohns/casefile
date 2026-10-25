from casefile.features.case_intake.domain.case import Case
from casefile.features.case_intake.domain.errors import IntakeError, IntakeErrorCode
from casefile.shared.ids import CaseId
from casefile.shared.result import Err, Ok, Result


class InMemoryCaseRepository:
    def __init__(self) -> None:
        self.cases: dict[CaseId, Case] = {}
        self.fail_writes = False

    async def get(self, case_id: CaseId) -> Case | None:
        return self.cases.get(case_id)

    async def list(self, *, limit: int = 50, offset: int = 0) -> tuple[Case, ...]:
        ordered = sorted(
            self.cases.values(), key=lambda case: (case.opened_at, case.id), reverse=True
        )
        return tuple(ordered[offset : offset + limit])

    async def add(self, case: Case) -> Result[Case, IntakeError]:
        if case.id in self.cases:
            return Err(IntakeError(IntakeErrorCode.CASE_ALREADY_EXISTS, "Case ID already exists."))
        if any(existing.alert_id == case.alert_id for existing in self.cases.values()):
            return Err(
                IntakeError(IntakeErrorCode.ALERT_ALREADY_EXISTS, "Alert already has a case.")
            )
        if self.fail_writes:
            raise RuntimeError("Simulated case storage failure")
        self.cases[case.id] = case
        return Ok(case)
