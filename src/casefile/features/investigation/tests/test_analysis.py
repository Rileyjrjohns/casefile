import math
from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given
from hypothesis import strategies as st

from casefile.features.investigation.domain.analysis import (
    Analysis,
    Conclusion,
    Confidence,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.tests.factories import CONCLUSION_ID, conclusion, source, value
from casefile.shared.result import Err, Ok


@given(st.floats())
def test_confidence_accepts_exactly_finite_unit_interval(number: float) -> None:
    result = Confidence.create(number)
    assert isinstance(result, Ok) == (math.isfinite(number) and 0 <= number <= 1)
    if isinstance(result, Ok):
        assert result.value.value == number


@pytest.mark.parametrize("number", [0.0, 1.0, -0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_confidence_boundaries(number: float) -> None:
    assert isinstance(Confidence.create(number), Ok) == (0 <= number <= 1)


@pytest.mark.parametrize(
    ("tool", "record"), [("", "id"), (" \n", "id"), ("kyc", ""), ("kyc", "\t")]
)
def test_source_requires_tool_and_record(tool: str, record: str) -> None:
    assert isinstance(SourceReference.create(tool, record), Err)


def test_source_preserves_exact_record_identifier_and_version() -> None:
    reference = value(SourceReference.create("risk_model@v1", "account/001"))
    assert (reference.tool, reference.record_id) == ("risk_model@v1", "account/001")


def test_conclusion_requires_a_statement_and_at_least_one_source() -> None:
    confidence = value(Confidence.create(0.5))
    assert isinstance(Conclusion.create(CONCLUSION_ID, "Suspicious", confidence, ()), Err)
    assert isinstance(Conclusion.create(CONCLUSION_ID, " ", confidence, (source(),)), Err)
    assert isinstance(
        Conclusion.create(CONCLUSION_ID, "Suspicious", confidence, (source(), source())), Err
    )


def test_analysis_rejects_duplicate_conclusions_and_blank_summary() -> None:
    assert isinstance(Analysis.create(" ", (), Recommendation.CLOSE), Err)
    assert isinstance(
        Analysis.create("Summary", (conclusion(), conclusion()), Recommendation.CLOSE), Err
    )


def test_analysis_can_report_no_signals() -> None:
    report = value(Analysis.create("Aucun signal détecté.", (), Recommendation.CLOSE))
    assert report.conclusions == ()


def test_validated_values_cannot_be_constructed_unchecked_or_mutated() -> None:
    with pytest.raises(TypeError, match="factory"):
        Confidence()
    item = conclusion()
    for target, field in ((item, "statement"), (item.confidence, "value")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, "Changed")
