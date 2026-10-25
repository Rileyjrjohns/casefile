from datetime import UTC, datetime
from uuid import UUID

import pytest

from casefile.features.evaluation.adapters.outbound.memory import (
    InMemoryCorrectionLookup,
    InMemoryPromotionAccess,
    InMemoryReferenceRepository,
)
from casefile.features.evaluation.application.promote_correction import (
    PromoteCorrection,
    PromoteCorrectionCommand,
)
from casefile.features.evaluation.domain.reference import Correction
from casefile.shared.ids import CaseId, ConclusionId, ReferenceId, ReviewerId, ReviewId, RunId
from casefile.shared.result import Err, Ok

AT = datetime(2026, 10, 24, 12, tzinfo=UTC)
REVIEW = ReviewId(UUID(int=1))
CONCLUSION = ConclusionId(UUID(int=2))
ACTOR = ReviewerId(UUID(int=3))


class FixedClock:
    def now(self) -> datetime:
        return AT


def correction() -> Correction:
    return Correction(
        REVIEW,
        RunId(UUID(int=4)),
        CaseId(UUID(int=5)),
        CONCLUSION,
        "Original",
        "Corrigé",
        "Source relue",
        ReviewerId(UUID(int=6)),
        AT,
    )


def command(identifier: int = 7) -> PromoteCorrectionCommand:
    return PromoteCorrectionCommand(ReferenceId(UUID(int=identifier)), REVIEW, CONCLUSION, ACTOR)


@pytest.mark.asyncio
async def test_promotes_persisted_correction_with_provenance_and_deduplicates() -> None:
    source = InMemoryCorrectionLookup({(REVIEW, CONCLUSION): correction()})
    repository = InMemoryReferenceRepository()
    use_case = PromoteCorrection(source, repository, InMemoryPromotionAccess({ACTOR}), FixedClock())
    result = await use_case.execute(command())
    assert isinstance(result, Ok)
    assert result.value.correction == correction()
    assert result.value.promoted_by == ACTOR and result.value.promoted_at == AT
    assert await use_case.execute(command(8)) == result
    assert list(repository.examples.values()) == [result.value]


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized", [True, False])
async def test_missing_or_unauthorized_correction_does_not_create_reference(
    authorized: bool,
) -> None:
    repository = InMemoryReferenceRepository()
    result = await PromoteCorrection(
        InMemoryCorrectionLookup(),
        repository,
        InMemoryPromotionAccess({ACTOR} if authorized else set()),
        FixedClock(),
    ).execute(command())
    assert isinstance(result, Err) and repository.examples == {}


@pytest.mark.asyncio
async def test_reference_storage_failure_preserves_the_source() -> None:
    source = InMemoryCorrectionLookup({(REVIEW, CONCLUSION): correction()})
    repository = InMemoryReferenceRepository()
    repository.fail_writes = True
    with pytest.raises(RuntimeError, match="storage"):
        await PromoteCorrection(
            source, repository, InMemoryPromotionAccess({ACTOR}), FixedClock()
        ).execute(command())
    assert repository.examples == {}
    assert source.corrections[(REVIEW, CONCLUSION)] == correction()
