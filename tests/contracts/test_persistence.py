import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest

from casefile.features.case_intake.domain.case import Case
from casefile.features.evaluation.domain.reference import ReferenceExample
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.run import HumanDecision, RunState
from casefile.shared.ids import CaseId, ReferenceId, ReviewId, RunId
from casefile.shared.result import Err, Ok
from tests.contracts.support import (
    AT,
    CASE,
    CONCLUSION,
    REVIEW,
    REVIEWER,
    RUN,
    Backend,
    awaiting,
    case,
    pending,
    review,
    seed,
    value,
)

pytestmark = pytest.mark.asyncio


async def test_case_repository_contract(backend: Backend) -> None:
    assert await backend.cases.get(CASE) is None
    assert not await backend.case_lookup.exists(CASE)
    original = case()
    value(await backend.cases.add(original))
    assert await backend.cases.get(CASE) == original
    assert await backend.case_lookup.exists(CASE)
    assert isinstance(await backend.cases.add(original), Err)
    duplicate_alert = value(
        Case.create(
            CaseId(UUID(int=99)), original.alert_id, "other", original.kind, "Different", AT
        )
    )
    assert isinstance(await backend.cases.add(duplicate_alert), Err)
    assert await backend.cases.get(CASE) == original


async def test_run_creation_is_unique_and_queues_one_job(backend: Backend) -> None:
    await seed(backend)
    assert isinstance(await backend.runs.enqueue(pending()), Err)
    second = pending().new(RunId(UUID(int=99)), CASE, pending().limits)
    assert isinstance(await backend.runs.enqueue(second), Err)
    snapshot = await backend.runs.get(RUN)
    assert snapshot is not None and snapshot.run == pending() and snapshot.revision == 1
    lease = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None and lease.run_id == RUN
    assert await backend.queue.claim(lease_for=timedelta(seconds=30)) is None


async def test_run_save_roundtrip_and_optimistic_conflict(backend: Backend) -> None:
    assert isinstance(await backend.runs.save(awaiting(), expected_revision=1), Err)
    await seed(backend)
    saved = value(await backend.runs.save(awaiting(), expected_revision=1))
    assert saved.revision == 2 and saved.run == awaiting()
    assert await backend.runs.get(RUN) == saved
    assert isinstance(await backend.runs.save(pending(), expected_revision=1), Err)
    assert isinstance(await backend.runs.save(pending(), expected_revision=2), Err)
    failed = value(awaiting().fail("Tool unavailable"))
    value(await backend.runs.save(failed, expected_revision=2))
    snapshot = await backend.runs.get(RUN)
    assert snapshot is not None and snapshot.run == failed


@pytest.mark.parametrize("state", list(RunState))
async def test_all_run_states_survive_storage(backend: Backend, state: RunState) -> None:
    await seed(backend)
    run = pending()
    if state == RunState.GATHERING:
        run = value(run.start_gathering())
    elif state == RunState.ANALYZING:
        run = value(value(run.start_gathering()).start_analysis())
    elif state == RunState.AWAITING_REVIEW:
        run = awaiting()
    elif state == RunState.FAILED:
        run = value(run.fail("Unavailable"))
    elif state == RunState.CLOSED:
        value(await backend.runs.save(awaiting(), expected_revision=1))
        value(await backend.gateway.commit(review(), expected_revision=2))
        receipt = value(
            HumanDecision.create(
                RUN, REVIEW, REVIEWER, (CONCLUSION,), Recommendation.CLOSE, AT, confirmed=True
            )
        )
        run = value(awaiting().close(receipt))
    if state not in {RunState.PENDING, RunState.CLOSED}:
        value(await backend.runs.save(run, expected_revision=1))
    snapshot = await backend.runs.get(RUN)
    assert snapshot is not None and snapshot.run == run


async def test_review_gateway_and_reader_contract(backend: Backend) -> None:
    assert isinstance(await backend.gateway.load(RUN), Err)
    assert await backend.reviews.get(REVIEW) is None
    await seed(backend)
    assert isinstance(await backend.gateway.load(RUN), Err)
    value(await backend.runs.save(awaiting(), expected_revision=1))
    target = value(await backend.gateway.load(RUN))
    assert target.revision == 2 and target.conclusions[0].statement == "Homonymie probable"
    assert isinstance(await backend.gateway.commit(review(), expected_revision=1), Err)
    value(await backend.gateway.commit(review(), expected_revision=2))
    assert await backend.reviews.get(REVIEW) == review()
    snapshot = await backend.runs.get(RUN)
    assert snapshot is not None and snapshot.run.state == RunState.CLOSED
    assert isinstance(await backend.gateway.commit(review(), expected_revision=2), Err)


async def test_only_one_concurrent_review_commits(backend: Backend) -> None:
    await seed(backend, ready_for_review=True)
    other = review(ReviewId(UUID(int=99)))
    outcomes = await asyncio.gather(
        backend.gateway.commit(review(), expected_revision=2),
        backend.gateway.commit(other, expected_revision=2),
    )
    assert sum(isinstance(result, Ok) for result in outcomes) == 1
    assert sum(isinstance(result, Err) for result in outcomes) == 1
    records = (await backend.reviews.get(REVIEW), await backend.reviews.get(other.id))
    assert sum(record is not None for record in records) == 1


async def test_correction_and_reference_contract(backend: Backend) -> None:
    assert isinstance(await backend.corrections.get(REVIEW, CONCLUSION), Err)
    await seed(backend, ready_for_review=True)
    value(await backend.gateway.commit(review(), expected_revision=2))
    correction = value(await backend.corrections.get(REVIEW, CONCLUSION))
    reference_id = ReferenceId(UUID(int=7))
    example = value(ReferenceExample.create(reference_id, correction, REVIEWER, AT))
    assert await backend.references.get(reference_id) is None
    assert value(await backend.references.add_once(example)) == example
    assert await backend.references.get(reference_id) == example
    other_id = value(ReferenceExample.create(ReferenceId(UUID(int=8)), correction, REVIEWER, AT))
    assert value(await backend.references.add_once(other_id)) == example


async def test_job_lease_retry_ack_and_stale_owner(backend: Backend) -> None:
    await seed(backend)
    lease = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert lease is not None and lease.attempt == 1
    assert not await backend.queue.ack(replace(lease, token=UUID(int=99)))
    assert await backend.queue.retry(lease, reason="Temporary failure", delay=timedelta(0))
    fresh = await backend.queue.claim(lease_for=timedelta(seconds=30))
    assert fresh is not None and fresh.attempt == 2 and fresh.token != lease.token
    assert not await backend.queue.ack(lease)
    assert await backend.queue.ack(fresh)
    assert not await backend.queue.ack(fresh)
    assert await backend.queue.claim(lease_for=timedelta(seconds=30)) is None
