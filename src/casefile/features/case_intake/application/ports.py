from typing import Protocol

from casefile.features.case_intake.domain.case import Case
from casefile.features.case_intake.domain.errors import IntakeError
from casefile.shared.ids import CaseId
from casefile.shared.result import Result


class CaseRepository(Protocol):
    async def list(self, *, limit: int = 50, offset: int = 0) -> tuple[Case, ...]: ...

    async def get(self, case_id: CaseId) -> Case | None: ...

    async def add(self, case: Case) -> Result[Case, IntakeError]:
        """Atomically insert; duplicate case and normalized alert IDs are rejected."""
        ...
