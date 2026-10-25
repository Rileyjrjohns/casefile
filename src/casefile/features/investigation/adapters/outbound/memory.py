from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Err, Ok, Result


class InMemoryCaseLookup:
    def __init__(self, cases: set[CaseId] | None = None) -> None:
        self.cases = set(cases or ())

    async def exists(self, case_id: CaseId) -> bool:
        return case_id in self.cases


class InMemoryRunStore:
    def __init__(self) -> None:
        self.runs: dict[RunId, RunSnapshot] = {}
        self.jobs: list[RunId] = []
        self.fail_writes = False

    async def get(self, run_id: RunId) -> RunSnapshot | None:
        return self.runs.get(run_id)

    async def for_case(self, case_id: CaseId) -> tuple[RunSnapshot, ...]:
        return tuple(item for item in reversed(self.runs.values()) if item.run.case_id == case_id)

    async def enqueue(self, run: Run) -> Result[Run, InvestigationError]:
        if run.state != RunState.PENDING:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_TRANSITION, "Only pending runs may be queued."
                )
            )
        if run.id in self.runs:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.RUN_ALREADY_EXISTS, "Run ID already exists."
                )
            )
        if any(
            item.run.case_id == run.case_id
            and item.run.state not in {RunState.CLOSED, RunState.FAILED}
            for item in self.runs.values()
        ):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.ACTIVE_RUN_EXISTS, "Case already has an active run."
                )
            )
        if self.fail_writes:
            raise RuntimeError("Simulated run storage failure")
        # No await between these writes: atomic within one asyncio event loop.
        self.runs[run.id] = RunSnapshot(run, 1)
        self.jobs.append(run.id)
        return Ok(run)

    async def save(
        self, run: Run, *, expected_revision: int
    ) -> Result[RunSnapshot, InvestigationError]:
        existing = self.runs.get(run.id)
        if existing is None:
            return Err(
                InvestigationError(InvestigationErrorCode.RUN_NOT_FOUND, "Run does not exist.")
            )
        if existing.revision != expected_revision or existing.run.case_id != run.case_id:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.CONFLICT, "Run changed since it was read."
                )
            )
        progression = tuple(RunState)
        if (
            run.state == RunState.CLOSED
            or run.human_decision != existing.run.human_decision
            or progression.index(run.state) < progression.index(existing.run.state)
        ):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_TRANSITION,
                    "Runs cannot move backwards or bypass atomic review finalization.",
                )
            )
        if self.fail_writes:
            raise RuntimeError("Simulated run storage failure")
        snapshot = RunSnapshot(run, existing.revision + 1)
        self.runs[run.id] = snapshot
        return Ok(snapshot)
