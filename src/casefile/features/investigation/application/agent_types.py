"""Immutable agent boundary values and ports; no model or tracing SDK imports."""

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from casefile.features.investigation.application.decisions import DecisionRequest, DecisionResponse
from casefile.features.investigation.application.jobs import JobLease
from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.application.tools import (
    AlertRecord,
    CaseDocument,
    InvalidToolQuery,
    InvestigatorNote,
    KycProfile,
    SanctionEntry,
    ToolName,
    Transaction,
)
from casefile.features.investigation.domain.budget import BudgetUsage
from casefile.features.investigation.domain.errors import InvestigationError
from casefile.features.investigation.domain.run import Run
from casefile.shared.ids import CaseId, RunId
from casefile.shared.result import Result

type EvidenceRecord = (
    Transaction | KycProfile | AlertRecord | SanctionEntry | InvestigatorNote | CaseDocument
)


class RecordedCallFailure(RuntimeError):
    """A replayed technical failure preserves the original failure category."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def tool_input(tool: ToolName, query: str) -> str:
    return json.dumps({"query": query, "tool": tool.value}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class CaseContext:
    subject_id: str
    alert_id: str
    kind: str
    description: str


@dataclass(frozen=True, slots=True)
class EvidenceBatch:
    tool: ToolName
    query: str
    records: tuple[EvidenceRecord, ...]


@dataclass(frozen=True, slots=True)
class SourceLink:
    tool: str
    record_id: str


@dataclass(frozen=True, slots=True)
class Signal:
    key: str
    statement: str
    confidence: float
    sources: tuple[SourceLink, ...]


@dataclass(frozen=True, slots=True)
class ModelRequest:
    context: CaseContext
    evidence: tuple[EvidenceBatch, ...]
    signals: tuple[Signal, ...]
    max_output_tokens: int = 1024
    prompt_version: str = "casefile-narrative-v1"


@dataclass(frozen=True, slots=True)
class NarrativeStatement:
    key: str
    text: str
    sources: tuple[SourceLink, ...]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    model: str
    summary: str
    statements: tuple[NarrativeStatement, ...]
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal


@dataclass(frozen=True, slots=True)
class TraceEvent:
    sequence: int
    operation: str
    phase: Literal["started", "succeeded", "failed", "transition"]
    at: datetime
    input_json: str = "{}"
    output_json: str | None = None
    reserved: BudgetUsage | None = None
    actual: BudgetUsage | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    snapshot: RunSnapshot
    events: tuple[TraceEvent, ...]


class ExecutionJournal(Protocol):
    async def load(self, run_id: RunId) -> ExecutionSnapshot | None: ...

    async def checkpoint(
        self,
        lease: JobLease,
        run: Run,
        event: TraceEvent,
        *,
        expected_revision: int,
    ) -> Result[ExecutionSnapshot, InvestigationError]:
        """Atomically append an event and save the run, fenced by revision and active lease."""
        ...


class InvestigationContext(Protocol):
    async def get(self, case_id: CaseId) -> CaseContext | None: ...


class EvidenceGateway(Protocol):
    async def fetch(
        self, tool: ToolName, query: str
    ) -> Result[EvidenceBatch, InvalidToolQuery]: ...


class LanguageModel(Protocol):
    def quote(self, request: ModelRequest) -> BudgetUsage:
        """Return a conservative reservation without making a network call."""
        ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class AgentCodec(Protocol):
    def decision_request_json(self, value: DecisionRequest) -> str: ...
    def decision_response_json(self, value: DecisionResponse) -> str: ...
    def decision_response(self, payload: str) -> DecisionResponse: ...
    def context_json(self, value: CaseContext) -> str: ...
    def context(self, payload: str) -> CaseContext: ...
    def evidence_json(self, value: EvidenceBatch) -> str: ...
    def evidence(self, payload: str) -> EvidenceBatch: ...
    def request_json(self, value: ModelRequest) -> str: ...
    def request(self, payload: str) -> ModelRequest: ...
    def response_json(self, value: ModelResponse) -> str: ...
    def response(self, payload: str) -> ModelResponse: ...


class Telemetry(Protocol):
    def span(self, name: str, attributes: dict[str, str | int]) -> AbstractContextManager[None]: ...

    def event(self, name: str, attributes: dict[str, str | int]) -> None: ...
