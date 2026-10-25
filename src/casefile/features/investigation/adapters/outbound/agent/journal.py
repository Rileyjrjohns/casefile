"""Atomic in-memory execution checkpoints and shared persistence validation."""

import json

from casefile.features.investigation.adapters.outbound.memory import InMemoryRunStore
from casefile.features.investigation.adapters.outbound.queue_memory import InMemoryJobQueue
from casefile.features.investigation.application.agent_types import ExecutionSnapshot, TraceEvent
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.domain.run import Run, RunState
from casefile.shared.ids import RunId
from casefile.shared.result import Err, Ok, Result


def conflict() -> Err[InvestigationError]:
    return Err(
        InvestigationError(InvestigationErrorCode.CONFLICT, "Execution revision or lease changed.")
    )


def valid_checkpoint(before: ExecutionSnapshot, run: Run, event: TraceEvent, revision: int) -> bool:
    previous = before.snapshot.run
    if (
        before.snapshot.revision != revision
        or previous.id != run.id
        or previous.case_id != run.case_id
        or previous.limits != run.limits
        or previous.state in {RunState.CLOSED, RunState.FAILED, RunState.AWAITING_REVIEW}
        or run.state == RunState.CLOSED
        or run.human_decision != previous.human_decision
        or tuple(RunState).index(run.state) < tuple(RunState).index(previous.state)
        or event.sequence != len(before.events) + 1
        or event.at.utcoffset() is None
    ):
        return False
    for payload in (event.input_json, event.output_json):
        if payload is not None and (
            len(payload.encode()) > 1_000_000 or not isinstance(json.loads(payload), dict)
        ):
            raise ValueError("Execution payload must be a bounded JSON object.")
    return True


class MemoryExecutionJournal:
    def __init__(self, runs: InMemoryRunStore, queue: InMemoryJobQueue) -> None:
        self.runs = runs
        self.queue = queue
        self.events: dict[RunId, tuple[TraceEvent, ...]] = {}
        self.fail_writes = False

    async def load(self, run_id: RunId) -> ExecutionSnapshot | None:
        snapshot = self.runs.runs.get(run_id)
        return ExecutionSnapshot(snapshot, self.events.get(run_id, ())) if snapshot else None

    async def checkpoint(
        self,
        lease: JobLease,
        run: Run,
        event: TraceEvent,
        *,
        expected_revision: int,
    ) -> Result[ExecutionSnapshot, InvestigationError]:
        before = await self.load(lease.run_id)
        if (
            before is None
            or not self.queue.owns(lease)
            or not valid_checkpoint(
                before,
                run,
                event,
                expected_revision,
            )
        ):
            return conflict()
        if self.fail_writes or self.runs.fail_writes:
            raise RuntimeError("Simulated execution storage failure")
        after = ExecutionSnapshot(RunSnapshot(run, expected_revision + 1), (*before.events, event))
        self.runs.runs[run.id] = after.snapshot
        self.events[run.id] = after.events
        return Ok(after)
