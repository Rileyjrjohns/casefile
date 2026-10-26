"""Read immutable, versioned reference fixtures and strict regression thresholds."""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from casefile.features.evaluation.application.benchmark import BenchmarkCase
from casefile.features.evaluation.domain.benchmark import (
    Citation,
    ExpectedAnalysis,
    ReferenceSignal,
    Thresholds,
)

type Text = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000)]
type Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CitationDocument(Document):
    tool: Text
    record_id: Text


class SignalDocument(Document):
    key: Text
    statement: Text
    confidence: Ratio
    sources: Annotated[tuple[CitationDocument, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_sources(self) -> Self:
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("Reference citations must be unique.")
        return self


class AnalysisDocument(Document):
    summary: Text
    signals: tuple[SignalDocument, ...]
    recommendation: Literal["close", "escalate", "request_information"]

    @model_validator(mode="after")
    def unique_signals(self) -> Self:
        if len({signal.key for signal in self.signals}) != len(self.signals):
            raise ValueError("Reference signal keys must be unique.")
        return self

    def to_expected(self) -> ExpectedAnalysis:
        return ExpectedAnalysis(
            self.summary,
            tuple(
                ReferenceSignal(
                    signal.key,
                    signal.statement,
                    signal.confidence,
                    tuple(Citation(source.tool, source.record_id) for source in signal.sources),
                )
                for signal in self.signals
            ),
            self.recommendation,
        )


class GoldenDocument(Document):
    schema_version: Literal[1]
    fictional: Literal[True]
    id: Annotated[str, StringConstraints(pattern=r"^(case|current)-[0-9]{3}$", strict=True)]
    category: Annotated[str, StringConstraints(pattern=r"^[a-z_]+$", strict=True)]
    rationale: Text
    context: dict[str, JsonValue]
    evidence: tuple[dict[str, JsonValue], ...]
    expected: AnalysisDocument


class ThresholdDocument(Document):
    schema_version: Literal[1]
    min_cases: Annotated[int, Field(ge=30, strict=True)]
    min_signal_precision: Ratio
    min_signal_recall: Ratio
    min_recommendation_accuracy: Ratio
    # Zero is an invariant, not a tunable tolerance.
    max_invalid_source_rate: Annotated[float, Field(ge=0, le=0, allow_inf_nan=False)]
    max_cost_per_case: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    max_latency_ms: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    def to_thresholds(self) -> Thresholds:
        return Thresholds(
            self.min_cases,
            self.min_signal_precision,
            self.min_signal_recall,
            self.min_recommendation_accuracy,
            self.max_invalid_source_rate,
            self.max_cost_per_case,
            self.max_latency_ms,
        )


@dataclass(frozen=True, slots=True)
class GoldenSuite:
    cases: tuple[BenchmarkCase, ...]
    sha256: str


def read_fixture(path: Path) -> str:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Evaluation fixture exceeds the storage limit.")
    return path.read_text(encoding="utf-8")


def load_suite(root: Path) -> GoldenSuite:
    paths = sorted((root / "golden").glob("*.json"))
    if len(paths) < 30:
        raise ValueError("The golden dataset requires at least 30 cases.")
    digest = hashlib.sha256()
    cases: list[BenchmarkCase] = []
    for path in paths:
        raw = read_fixture(path)
        document = GoldenDocument.model_validate_json(raw)
        if document.id != path.stem:
            raise ValueError("Golden case identity must match its filename.")
        replay = read_fixture(root / "replay" / path.name)
        # Hash exact bytes and filenames, with boundaries, for reproducibility.
        for part in (path.name, raw, replay):
            digest.update(part.encode())
            digest.update(b"\x00")
        scenario = json.dumps({"context": document.context, "evidence": document.evidence})
        cases.append(
            BenchmarkCase(
                document.id,
                document.category,
                document.rationale,
                document.expected.to_expected(),
                scenario,
                replay,
            )
        )
    return GoldenSuite(tuple(cases), digest.hexdigest())


def load_thresholds(path: Path) -> Thresholds:
    with path.open("rb") as source:
        return ThresholdDocument.model_validate(tomllib.load(source)).to_thresholds()
