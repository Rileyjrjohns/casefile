"""Evaluate every reference through an injected runner and retain all failures."""

from dataclasses import dataclass
from typing import Literal, Protocol

from casefile.features.evaluation.domain.benchmark import (
    CaseScore,
    ExpectedAnalysis,
    Metrics,
    Observation,
    Thresholds,
    aggregate,
    assess,
    violations,
)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    category: str
    rationale: str
    expected: ExpectedAnalysis
    scenario_json: str
    replay_json: str


class CaseRunner(Protocol):
    async def run(self, case: BenchmarkCase) -> Observation: ...


@dataclass(frozen=True, slots=True)
class SuiteReport:
    schema_version: int
    mode: Literal["replay", "http"]
    dataset_sha256: str
    profile: str
    findings_policy: str
    thresholds: Thresholds
    metrics: Metrics
    cases: tuple[CaseScore, ...]
    violations: tuple[str, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class EvaluateSuite:
    runner: CaseRunner

    async def execute(
        self,
        cases: tuple[BenchmarkCase, ...],
        thresholds: Thresholds,
        *,
        mode: Literal["replay", "http"],
        dataset_sha256: str,
        profile: str,
        findings_policy: str,
    ) -> SuiteReport:
        scores = tuple(
            [
                assess(case.id, case.category, case.expected, await self.runner.run(case))
                for case in cases
            ]
        )
        failures = violations(scores, thresholds)
        return SuiteReport(
            1,
            mode,
            dataset_sha256,
            profile,
            findings_policy,
            thresholds,
            aggregate(scores),
            scores,
            failures,
            not failures,
        )
