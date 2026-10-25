from dataclasses import dataclass
from typing import Protocol

from casefile.features.investigation.domain.errors import InvestigationError
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Result


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run: Run
    revision: int


class CaseLookup(Protocol):
    async def exists(self, case_id: CaseId) -> bool: ...


class RunStore(Protocol):
    async def for_case(self, case_id: CaseId) -> tuple[RunSnapshot, ...]:
        """List newest-created runs first; subsequent updates never reorder history."""
        ...

    async def get(self, run_id: RunId) -> RunSnapshot | None: ...

    async def enqueue(self, run: Run) -> Result[Run, InvestigationError]:
        """Atomically insert a PENDING run and its job, with one active run per case."""
        ...

    async def save(
        self, run: Run, *, expected_revision: int
    ) -> Result[RunSnapshot, InvestigationError]:
        """Compare-and-swap a worker snapshot; forbid backwards states and direct closure."""
        ...
