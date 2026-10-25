from dataclasses import dataclass

from casefile.features.case_intake.application.ports import CaseRepository
from casefile.features.case_intake.domain.case import Case
from casefile.shared.ids import CaseId


@dataclass(frozen=True, slots=True)
class CaseQueries:
    repository: CaseRepository

    async def list(self, *, limit: int = 50, offset: int = 0) -> tuple[Case, ...]:
        return await self.repository.list(limit=limit, offset=offset)

    async def get(self, case_id: CaseId) -> Case | None:
        return await self.repository.get(case_id)
