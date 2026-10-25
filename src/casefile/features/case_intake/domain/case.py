from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from casefile.features.case_intake.domain.errors import IntakeError, IntakeErrorCode
from casefile.shared.ids import CaseId
from casefile.shared.immutable import FactoryOnly
from casefile.shared.result import Err, Ok, Result


class AlertKind(StrEnum):
    UNUSUAL_TRANSACTION = "unusual_transaction"
    SANCTIONS_MATCH = "sanctions_match"
    KYC_INCONSISTENCY = "kyc_inconsistency"
    PAYMENT_FRAUD = "payment_fraud"
    CREDIT_DOCUMENT_FRAUD = "credit_document_fraud"
    SANCTIONS_IDENTITY_REVIEW = "sanctions_identity_review"


@dataclass(frozen=True, slots=True, init=False)
class Case(FactoryOnly):
    id: CaseId
    alert_id: str
    subject_id: str
    kind: AlertKind
    description: str
    opened_at: datetime

    @classmethod
    def create(
        cls,
        id: CaseId,
        alert_id: str,
        subject_id: str,
        kind: AlertKind,
        description: str,
        opened_at: datetime,
    ) -> Result["Case", IntakeError]:
        if (
            not all(value.strip() for value in (alert_id, subject_id, description))
            or opened_at.utcoffset() is None
        ):
            return Err(
                IntakeError(
                    IntakeErrorCode.INVALID_CASE,
                    "An alert, subject, description and aware timestamp are required.",
                )
            )
        return Ok(
            cls._new(
                id=id,
                alert_id=alert_id.strip(),
                subject_id=subject_id.strip(),
                kind=kind,
                description=description.strip(),
                opened_at=opened_at.astimezone(UTC),
            )
        )
