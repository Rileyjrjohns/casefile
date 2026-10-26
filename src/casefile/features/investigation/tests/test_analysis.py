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
from casefile.features.investigation.domain.errors import InvestigationErrorCode
from casefile.features.investigation.tests.factories import (
    CONCLUSION_ID,
    conclusion,
    error_code,
    source,
    value,
)
from casefile.shared.result import Ok


@given(st.floats())
def test_confidence_accepts_exactly_finite_unit_interval(number: float) -> None:
    result = Confidence.create(number)
    assert isinstance(result, Ok) == (math.isfinite(number) and 0 <= number <= 1)
    if isinstance(result, Ok):
        assert result.value.value == number
    else:
        assert error_code(result) == InvestigationErrorCode.INVALID_CONFIDENCE


@pytest.mark.parametrize("number", [0.0, 1.0, -0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_confidence_boundaries(number: float) -> None:
    result = Confidence.create(number)
    assert isinstance(result, Ok) == (0 <= number <= 1)
    if not isinstance(result, Ok):
        assert error_code(result) == InvestigationErrorCode.INVALID_CONFIDENCE


@pytest.mark.parametrize(
    ("tool", "record"), [("", "id"), (" \n", "id"), ("kyc", ""), ("kyc", "\t")]
)
def test_source_requires_tool_and_record(tool: str, record: str) -> None:
    assert error_code(SourceReference.create(tool, record)) == InvestigationErrorCode.INVALID_SOURCE


def test_source_preserves_exact_record_identifier_and_version() -> None:
    reference = value(SourceReference.create("risk_model@v1", "account/001"))
    assert (reference.tool, reference.record_id) == ("risk_model@v1", "account/001")


def test_conclusion_requires_a_statement_and_at_least_one_source() -> None:
    confidence = value(Confidence.create(0.5))
    invalid = InvestigationErrorCode.INVALID_CONCLUSION
    assert error_code(Conclusion.create(CONCLUSION_ID, "Suspicious", confidence, ())) == invalid
    assert error_code(Conclusion.create(CONCLUSION_ID, " ", confidence, (source(),))) == invalid
    duplicated = Conclusion.create(CONCLUSION_ID, "Suspicious", confidence, (source(), source()))
    assert error_code(duplicated) == invalid


def test_conclusion_retains_what_it_was_given() -> None:
    confidence = value(Confidence.create(0.5))
    other = value(SourceReference.create("kyc", "kyc-001"))
    item = value(Conclusion.create(CONCLUSION_ID, "Suspicious", confidence, (source(), other)))
    assert item.id == CONCLUSION_ID
    assert item.statement == "Suspicious"
    assert item.confidence == confidence
    assert item.sources == (source(), other)


def test_analysis_rejects_duplicate_conclusions_and_blank_summary() -> None:
    invalid = InvestigationErrorCode.INVALID_ANALYSIS
    assert error_code(Analysis.create(" ", (), Recommendation.CLOSE)) == invalid
    duplicated = Analysis.create("Summary", (conclusion(), conclusion()), Recommendation.CLOSE)
    assert error_code(duplicated) == invalid


def test_analysis_retains_what_it_was_given() -> None:
    report = value(Analysis.create("Summary", (conclusion(),), Recommendation.REQUEST_INFORMATION))
    assert report.summary == "Summary"
    assert report.conclusions == (conclusion(),)
    assert report.recommendation == Recommendation.REQUEST_INFORMATION


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
