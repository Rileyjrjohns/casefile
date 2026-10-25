"""Transactional cross-feature bridges and PostgreSQL composition."""

from dataclasses import dataclass

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casefile.bootstrap.memory import StoredCaseLookup
from casefile.features.case_intake.adapters.outbound.sql import SqlCaseRepository
from casefile.features.case_intake.application.open_case import OpenCase
from casefile.features.evaluation.adapters.outbound.sql import SqlReferenceRepository
from casefile.features.evaluation.application.ports import PromotionAccess
from casefile.features.evaluation.application.promote_correction import PromoteCorrection
from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.features.evaluation.domain.reference import Correction
from casefile.features.investigation.adapters.outbound.agent.journal_sql import SqlExecutionJournal
from casefile.features.investigation.adapters.outbound.documents import encode_run
from casefile.features.investigation.adapters.outbound.queue_sql import SqlJobQueue
from casefile.features.investigation.adapters.outbound.sql import RunRow, SqlRunStore
from casefile.features.investigation.application.start_run import StartRun
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.run import HumanDecision, RunState
from casefile.features.review.adapters.outbound.sql import (
    ReviewDocument,
    ReviewRow,
    SqlReviewReader,
)
from casefile.features.review.application.ports import (
    ReviewConclusion,
    ReviewerAccess,
    ReviewTarget,
)
from casefile.features.review.application.record_review import RecordReview
from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.features.review.domain.review import Review, ReviewAction
from casefile.shared.clock import Clock
from casefile.shared.ids import ConclusionId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class SqlReviewGateway:
    sessions: async_sessionmaker[AsyncSession]

    async def load(self, run_id: RunId) -> Result[ReviewTarget, ReviewError]:
        async with self.sessions() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return Err(ReviewError(ReviewErrorCode.RUN_NOT_FOUND, "Run does not exist."))
            run = row.snapshot().run
            if run.state != RunState.AWAITING_REVIEW:
                return Err(
                    ReviewError(ReviewErrorCode.RUN_NOT_REVIEWABLE, "Run is not awaiting review.")
                )
            assert run.analysis is not None
            return Ok(
                ReviewTarget(
                    run_id,
                    row.revision,
                    tuple(
                        ReviewConclusion(item.id, item.statement)
                        for item in run.analysis.conclusions
                    ),
                )
            )

    async def commit(
        self, review: Review, *, expected_revision: int
    ) -> Result[Review, ReviewError]:
        try:
            async with self.sessions.begin() as session:
                row = await session.scalar(
                    select(RunRow).where(RunRow.id == review.run_id).with_for_update()
                )
                if (
                    row is None
                    or row.revision != expected_revision
                    or row.state != RunState.AWAITING_REVIEW.value
                ):
                    return Err(
                        ReviewError(
                            ReviewErrorCode.CONFLICT, "Run is no longer available at this revision."
                        )
                    )
                if await session.get(ReviewRow, review.id) is not None:
                    return Err(
                        ReviewError(ReviewErrorCode.REVIEW_ID_CONFLICT, "Review ID already exists.")
                    )
                run = row.snapshot().run
                assert run.analysis is not None
                if {item.id: item.statement for item in run.analysis.conclusions} != {
                    item.conclusion_id: item.original_statement for item in review.conclusions
                }:
                    return Err(
                        ReviewError(
                            ReviewErrorCode.CONFLICT, "Review originals do not match the run."
                        )
                    )
                receipt = HumanDecision.create(
                    review.run_id,
                    review.id,
                    review.reviewer_id,
                    tuple(item.conclusion_id for item in review.conclusions),
                    Recommendation(review.decision.value),
                    review.submitted_at,
                    confirmed=True,
                )
                assert isinstance(receipt, Ok)
                closed = run.close(receipt.value)
                assert isinstance(closed, Ok)
                row.payload = encode_run(closed.value)
                row.state = RunState.CLOSED.value
                row.revision += 1
                # Flush the update first: failure of the following INSERT must undo it.
                await session.flush()
                session.add(
                    ReviewRow(
                        id=review.id,
                        run_id=review.run_id,
                        payload=ReviewDocument.model_validate(review).model_dump(mode="json"),
                    )
                )
            return Ok(review)
        except IntegrityError as error:
            if (
                isinstance(error.orig, UniqueViolation)
                and error.orig.diag.constraint_name == "reviews_pkey"
            ):
                return Err(
                    ReviewError(ReviewErrorCode.REVIEW_ID_CONFLICT, "Review ID already exists.")
                )
            raise


@dataclass(frozen=True, slots=True)
class SqlCorrectionLookup:
    sessions: async_sessionmaker[AsyncSession]

    async def get(
        self, review_id: ReviewId, conclusion_id: ConclusionId
    ) -> Result[Correction, EvaluationError]:
        async with self.sessions() as session:
            row = await session.get(ReviewRow, review_id)
            if row is None:
                return Err(
                    EvaluationError(
                        EvaluationErrorCode.CORRECTION_NOT_FOUND, "Review does not exist."
                    )
                )
            review = row.to_domain()
            run_row = await session.get(RunRow, review.run_id)
            if run_row is None:
                raise ValueError("Stored review has no run")
            run = run_row.snapshot().run
            if run.human_decision is None or run.human_decision.review_id != review.id:
                return Err(
                    EvaluationError(
                        EvaluationErrorCode.CORRECTION_NOT_FOUND,
                        "Review is not committed with its run.",
                    )
                )
            for item in review.conclusions:
                if item.conclusion_id != conclusion_id:
                    continue
                if item.action != ReviewAction.CORRECT:
                    return Err(
                        EvaluationError(
                            EvaluationErrorCode.NOT_A_CORRECTION,
                            "Only corrected conclusions can become reference examples.",
                        )
                    )
                assert item.corrected_statement is not None and item.reason is not None
                return Ok(
                    Correction(
                        review.id,
                        review.run_id,
                        run.case_id,
                        item.conclusion_id,
                        item.original_statement,
                        item.corrected_statement,
                        item.reason,
                        review.reviewer_id,
                        review.submitted_at,
                    )
                )
            return Err(
                EvaluationError(
                    EvaluationErrorCode.CORRECTION_NOT_FOUND,
                    "Conclusion is not part of the review.",
                )
            )


@dataclass(frozen=True, slots=True)
class PostgresApplication:
    cases: SqlCaseRepository
    runs: SqlRunStore
    reviews: SqlReviewReader
    references: SqlReferenceRepository
    review_gateway: SqlReviewGateway
    correction_lookup: SqlCorrectionLookup
    queue: SqlJobQueue
    open_case: OpenCase
    start_run: StartRun
    record_review: RecordReview
    promote_correction: PromoteCorrection
    journal: SqlExecutionJournal


def build_postgres_application(
    sessions: async_sessionmaker[AsyncSession],
    clock: Clock,
    reviewer_access: ReviewerAccess,
    promotion_access: PromotionAccess,
) -> PostgresApplication:
    cases = SqlCaseRepository(sessions)
    runs = SqlRunStore(sessions)
    reviews = SqlReviewReader(sessions)
    references = SqlReferenceRepository(sessions)
    gateway = SqlReviewGateway(sessions)
    corrections = SqlCorrectionLookup(sessions)
    return PostgresApplication(
        cases,
        runs,
        reviews,
        references,
        gateway,
        corrections,
        SqlJobQueue(sessions),
        OpenCase(cases, clock),
        StartRun(StoredCaseLookup(cases), runs),
        RecordReview(gateway, reviewer_access, clock),
        PromoteCorrection(corrections, references, promotion_access, clock),
        SqlExecutionJournal(sessions),
    )
