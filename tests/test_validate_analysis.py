"""Model output is accepted only as a one-to-one, exactly sourced view of the approved signals."""

from decimal import Decimal
from uuid import UUID, uuid5

import pytest

from casefile.features.investigation.application.agent_policy import validate_analysis
from casefile.features.investigation.application.agent_types import (
    ModelResponse,
    NarrativeStatement,
    Signal,
    SourceLink,
)
from casefile.features.investigation.application.tools import ToolName
from casefile.features.investigation.domain.analysis import (
    Analysis,
    Recommendation,
    SourceReference,
)
from casefile.features.investigation.domain.errors import InvestigationError, InvestigationErrorCode
from casefile.features.investigation.tests.factories import error_code, value
from casefile.shared.ids import RunId
from casefile.shared.result import Err, Result

RUN = RunId(UUID(int=42))
TXN = SourceLink(ToolName.TRANSACTIONS, "txn-1")
KYC = SourceLink(ToolName.KYC, "kyc-1")
ALERT = SourceLink(ToolName.ALERTS, "alert-1")
VOLUME = Signal(
    "volume:txn-1", "Transfer txn-1 exceeds the declared monthly volume.", 0.8, (TXN, KYC)
)
KYC_ALERT = Signal(
    "kyc:alert-1", "An open alert reports inconsistent KYC information.", 0.7, (ALERT,)
)
OBSERVED = tuple(
    value(SourceReference.create(link.tool, link.record_id)) for link in (ALERT, KYC, TXN)
)


def statement(signal: Signal, sources: tuple[SourceLink, ...] | None = None) -> NarrativeStatement:
    return NarrativeStatement(
        signal.key,
        "Model wording that is never shown.",
        signal.sources if sources is None else sources,
    )


def response(
    *statements: NarrativeStatement, summary: str = "Two signals were found for review."
) -> ModelResponse:
    return ModelResponse("fake-model", summary, statements, 10, 10, Decimal(0))


def validate(
    model: ModelResponse,
    signals: tuple[Signal, ...] = (VOLUME, KYC_ALERT),
    recommendation: Recommendation = Recommendation.ESCALATE,
) -> Result[Analysis, InvestigationError]:
    return validate_analysis(RUN, signals, recommendation, model, OBSERVED)


def failure(result: Result[Analysis, InvestigationError]) -> InvestigationError:
    assert isinstance(result, Err), result
    return result.error


def test_a_complete_response_becomes_an_analysis_of_the_approved_signals() -> None:
    analysis = value(validate(response(statement(KYC_ALERT), statement(VOLUME))))
    assert analysis.summary == "Two signals were found for review."
    assert analysis.recommendation == Recommendation.ESCALATE
    assert [item.id for item in analysis.conclusions] == [
        uuid5(RUN, KYC_ALERT.key),
        uuid5(RUN, VOLUME.key),
    ]
    assert [item.statement for item in analysis.conclusions] == [
        KYC_ALERT.statement,
        VOLUME.statement,
    ]
    assert [item.confidence.value for item in analysis.conclusions] == [0.7, 0.8]
    assert [[(s.tool, s.record_id) for s in item.sources] for item in analysis.conclusions] == [
        [(ToolName.ALERTS, "alert-1")],
        [(ToolName.KYC, "kyc-1"), (ToolName.TRANSACTIONS, "txn-1")],
    ]


def test_sources_are_the_observed_references_in_observation_order() -> None:
    reordered = statement(VOLUME, (TXN, KYC))
    analysis = value(validate(response(statement(KYC_ALERT), reordered)))
    assert analysis.conclusions[1].sources == tuple(
        source for source in OBSERVED if source.record_id in {"kyc-1", "txn-1"}
    )


def test_no_signals_and_no_statements_is_a_valid_empty_analysis() -> None:
    analysis = value(
        validate(response(summary="No signals were detected."), (), Recommendation.CLOSE)
    )
    assert analysis.conclusions == ()
    assert analysis.recommendation == Recommendation.CLOSE


@pytest.mark.parametrize(
    "statements",
    [
        pytest.param((), id="no-statement"),
        pytest.param((statement(VOLUME),), id="missing-signal"),
        pytest.param(
            (statement(VOLUME), statement(KYC_ALERT), statement(VOLUME)), id="duplicate-key"
        ),
        pytest.param(
            (
                statement(VOLUME),
                statement(KYC_ALERT),
                NarrativeStatement("invented:key", "x", (ALERT,)),
            ),
            id="extra-statement",
        ),
        pytest.param(
            (statement(VOLUME), NarrativeStatement("invented:key", "x", (ALERT,))),
            id="other-key-instead-of-signal",
        ),
    ],
)
def test_statements_must_cover_exactly_the_approved_signals(
    statements: tuple[NarrativeStatement, ...],
) -> None:
    error = failure(validate(response(*statements)))
    assert error.code == InvestigationErrorCode.UNKNOWN_SOURCE
    assert "cover exactly the approved signals" in error.message


UNOBSERVED = SourceLink(ToolName.NOTES, "note-9")


@pytest.mark.parametrize(
    "cited",
    [
        pytest.param((), id="no-source"),
        pytest.param((TXN, TXN, KYC), id="repeated-source"),
        pytest.param((TXN,), id="subset-of-the-signal-sources"),
        pytest.param((TXN, KYC, ALERT), id="observed-but-unrelated-extra"),
        pytest.param((ALERT,), id="observed-but-unrelated"),
        pytest.param((TXN, UNOBSERVED), id="unobserved-extra"),
        pytest.param((UNOBSERVED,), id="unobserved"),
        pytest.param((SourceLink("kyc", "txn-1"), KYC), id="right-record-wrong-tool"),
    ],
)
def test_a_statement_must_cite_exactly_the_sources_of_its_signal(
    cited: tuple[SourceLink, ...],
) -> None:
    result = validate(response(statement(VOLUME, cited), statement(KYC_ALERT)))
    error = failure(result)
    assert error.code == InvestigationErrorCode.UNKNOWN_SOURCE
    assert "unobserved or unrelated source" in error.message


def test_a_signal_source_missing_from_the_observations_is_rejected_even_if_cited() -> None:
    result = validate_analysis(
        RUN,
        (VOLUME,),
        Recommendation.ESCALATE,
        response(statement(VOLUME)),
        (value(SourceReference.create(TXN.tool, TXN.record_id)),),
    )
    assert error_code(result) == InvestigationErrorCode.UNKNOWN_SOURCE


def test_an_invalid_signal_confidence_is_reported_as_such() -> None:
    broken = Signal("volume:txn-1", "Statement", 1.5, (TXN,))
    result = validate(response(statement(broken)), (broken,))
    assert error_code(result) == InvestigationErrorCode.INVALID_CONFIDENCE


def test_a_summary_that_contradicts_the_recommendation_is_rejected() -> None:
    contradicting = response(
        statement(VOLUME), statement(KYC_ALERT), summary="We recommend closing the case."
    )
    error = failure(validate(contradicting))
    assert error.code == InvestigationErrorCode.INVALID_ANALYSIS
    assert error.message.startswith("Model summary contradicts the analysis:")


def test_a_blank_summary_is_rejected_by_the_analysis_itself() -> None:
    blank = response(statement(VOLUME), statement(KYC_ALERT), summary=" ")
    assert error_code(validate(blank)) == InvestigationErrorCode.INVALID_ANALYSIS


def test_source_problems_are_reported_before_summary_problems() -> None:
    contradicting = response(
        statement(VOLUME, (UNOBSERVED,)),
        statement(KYC_ALERT),
        summary="We recommend closing the case.",
    )
    assert error_code(validate(contradicting)) == InvestigationErrorCode.UNKNOWN_SOURCE


def test_citing_every_observed_source_and_nothing_else_is_accepted() -> None:
    observed = tuple(
        value(SourceReference.create(link.tool, link.record_id)) for link in (TXN, KYC)
    )
    result = validate_analysis(
        RUN, (VOLUME,), Recommendation.ESCALATE, response(statement(VOLUME)), observed
    )
    assert value(result).conclusions[0].sources == observed
