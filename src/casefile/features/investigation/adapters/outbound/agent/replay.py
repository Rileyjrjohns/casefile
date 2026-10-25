"""Replay completed attempts and interruptions in their original per-operation order."""

import asyncio

from casefile.features.investigation.application.agent_types import RecordedCallFailure, TraceEvent


class ReplayTape:
    def __init__(self, events: tuple[TraceEvent, ...]) -> None:
        self.attempts: dict[str, list[TraceEvent]] = {}
        self.positions: dict[str, int] = {}
        pending: dict[str, TraceEvent] = {}
        for event in events:
            if event.phase == "started":
                if event.operation in pending:
                    raise ValueError("Replay trace contains overlapping unfinished calls.")
                pending[event.operation] = event
            elif event.phase in {"succeeded", "failed"} and event.operation in pending:
                start = pending.pop(event.operation)
                if event.input_json != start.input_json or event.reserved != start.reserved:
                    raise ValueError("Replay call does not match its reservation.")
                self.attempts.setdefault(event.operation, []).append(event)
        for operation, event in pending.items():
            self.attempts.setdefault(operation, []).append(event)

    def peek(self, operation: str, input_json: str) -> TraceEvent:
        position = self.positions.get(operation, 0)
        attempts = self.attempts.get(operation, [])
        if position >= len(attempts) or attempts[position].input_json != input_json:
            raise ValueError("Replay request is absent, exhausted or different from the recording.")
        return attempts[position]

    def consume(self, operation: str, input_json: str) -> TraceEvent:
        event = self.peek(operation, input_json)
        self.positions[operation] = self.positions.get(operation, 0) + 1
        if event.phase == "started":
            raise asyncio.CancelledError("Recorded interruption")
        if event.phase == "failed" and not (event.error or "").startswith("query:"):
            raise RecordedCallFailure(event.error or "ProviderFailure")
        return event
