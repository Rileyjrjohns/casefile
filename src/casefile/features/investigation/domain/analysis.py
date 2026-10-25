"""Sourced, immutable analysis values; no provider or transport dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.shared.ids import ConclusionId
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


class Recommendation(StrEnum):
    CLOSE = "close"
    ESCALATE = "escalate"
    REQUEST_INFORMATION = "request_information"


@dataclass(frozen=True, slots=True, init=False)
class Confidence(FactoryOnly):
    value: float

    @classmethod
    def create(cls, value: float) -> Result["Confidence", InvestigationError]:
        if not isfinite(value) or not 0 <= value <= 1:
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_CONFIDENCE,
                    "Confidence must be finite and between 0 and 1.",
                )
            )
        return Ok(cls._new(value=value))


@dataclass(frozen=True, slots=True, init=False)
class SourceReference(FactoryOnly):
    tool: str
    record_id: str

    @classmethod
    def create(cls, tool: str, record_id: str) -> Result["SourceReference", InvestigationError]:
        if not tool.strip() or not record_id.strip():
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_SOURCE,
                    "A source requires an exact tool name and record identifier.",
                )
            )
        return Ok(cls._new(tool=tool, record_id=record_id))


@dataclass(frozen=True, slots=True, init=False)
class Conclusion(FactoryOnly):
    id: ConclusionId
    statement: str
    confidence: Confidence
    sources: tuple[SourceReference, ...]

    @classmethod
    def create(
        cls,
        id: ConclusionId,
        statement: str,
        confidence: Confidence,
        sources: tuple[SourceReference, ...],
    ) -> Result["Conclusion", InvestigationError]:
        if not statement.strip() or not sources or len(set(sources)) != len(sources):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_CONCLUSION,
                    "A conclusion requires a statement and at least one unique source.",
                )
            )
        return Ok(
            cls._new(id=id, statement=statement, confidence=confidence, sources=tuple(sources))
        )


@dataclass(frozen=True, slots=True, init=False)
class Analysis(FactoryOnly):
    summary: str
    conclusions: tuple[Conclusion, ...]
    recommendation: Recommendation

    @classmethod
    def create(
        cls, summary: str, conclusions: tuple[Conclusion, ...], recommendation: Recommendation
    ) -> Result["Analysis", InvestigationError]:
        if not summary.strip() or len({item.id for item in conclusions}) != len(conclusions):
            return Err(
                InvestigationError(
                    InvestigationErrorCode.INVALID_ANALYSIS,
                    "An analysis requires a summary and distinct conclusion identifiers.",
                )
            )
        return Ok(
            cls._new(summary=summary, conclusions=tuple(conclusions), recommendation=recommendation)
        )
