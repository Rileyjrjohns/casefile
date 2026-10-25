from datetime import UTC, datetime
from uuid import UUID

import pytest

from casefile.features.case_intake.adapters.outbound.memory import InMemoryCaseRepository
from casefile.features.case_intake.application.open_case import OpenCase, OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.features.case_intake.domain.errors import IntakeErrorCode
from casefile.shared.ids import CaseId
from casefile.shared.result import Err, Ok

AT = datetime(2026, 10, 24, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


def command(identifier: int = 1, alert: str = "alert-001") -> OpenCaseCommand:
    return OpenCaseCommand(
        CaseId(UUID(int=identifier)),
        alert,
        "customer-001",
        AlertKind.UNUSUAL_TRANSACTION,
        "  Montant inhabituel  ",
    )


@pytest.mark.asyncio
async def test_open_case_normalizes_and_saves_a_timestamped_case() -> None:
    repository = InMemoryCaseRepository()
    result = await OpenCase(repository, FixedClock()).execute(command())
    assert isinstance(result, Ok)
    assert result.value.description == "Montant inhabituel"
    assert result.value.opened_at == AT
    assert await repository.get(result.value.id) == result.value
    assert await repository.get(CaseId(UUID(int=99))) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["alert_id", "subject_id", "description"])
async def test_invalid_input_never_reaches_storage(field: str) -> None:
    from dataclasses import replace

    repository = InMemoryCaseRepository()
    request = {
        "alert_id": replace(command(), alert_id=" \n"),
        "subject_id": replace(command(), subject_id=" \n"),
        "description": replace(command(), description=" \n"),
    }[field]
    result = await OpenCase(repository, FixedClock()).execute(request)
    assert isinstance(result, Err)
    assert result.error.code == IntakeErrorCode.INVALID_CASE
    assert repository.cases == {}


@pytest.mark.asyncio
async def test_duplicate_case_or_alert_does_not_overwrite_existing_data() -> None:
    repository = InMemoryCaseRepository()
    use_case = OpenCase(repository, FixedClock())
    original = await use_case.execute(command())
    assert isinstance(original, Ok)
    for request in (command(alert="different"), command(2)):
        assert isinstance(await use_case.execute(request), Err)
    assert list(repository.cases.values()) == [original.value]


@pytest.mark.asyncio
async def test_technical_failure_propagates_without_partial_write() -> None:
    repository = InMemoryCaseRepository()
    repository.fail_writes = True
    with pytest.raises(RuntimeError, match="storage"):
        await OpenCase(repository, FixedClock()).execute(command())
    assert repository.cases == {}
