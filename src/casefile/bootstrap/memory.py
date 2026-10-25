"""Local composition and cross-feature translations for a single asyncio loop.

These bridges share the in-memory stores to publish atomic changes without an
await in the commit section. Durable transactions belong to the SQL adapters.
"""

from dataclasses import dataclass

from casefile.features.case_intake.adapters.outbound.memory import InMemoryCaseRepository
from casefile.features.case_intake.application.open_case import OpenCase
from casefile.features.case_intake.application.ports import CaseRepository
from casefile.features.evaluation.adapters.outbound.memory import (
    InMemoryPromotionAccess,
    InMemoryReferenceRepository,
)
from casefile.features.evaluation.application.promote_correction import PromoteCorrection
from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.features.evaluation.domain.reference import Correction
from casefile.features.investigation.adapters.outbound.agent.journal import MemoryExecutionJournal
from casefile.features.investigation.adapters.outbound.memory import InMemoryRunStore
from casefile.features.investigation.adapters.outbound.queue_memory import InMemoryJobQueue
from casefile.features.investigation.application.ports import RunSnapshot
from casefile.features.investigation.application.start_run import StartRun
from casefile.features.investigation.domain.analysis import Recommendation
from casefile.features.investigation.domain.run import HumanDecision, RunState
from casefile.features.review.adapters.outbound.memory import (
    InMemoryReviewerAccess,
    InMemoryReviewStorage,
)
from casefile.features.review.application.ports import ReviewConclusion, ReviewTarget
from casefile.features.review.application.record_review import RecordReview
from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.features.review.domain.review import Review, ReviewAction
from casefile.shared.clock import Clock
from casefile.shared.ids import CaseId, ConclusionId, ReviewId, RunId
from casefile.shared.result import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class StoredCaseLookup:
    cases: CaseRepository

    async def exists(self, case_id: CaseId) -> bool:
        return await self.cases.get(case_id) is not None


@dataclass(frozen=True, slots=True)
class ConnectedReviewGateway:
    runs: InMemoryRunStore
    reviews: InMemoryReviewStorage

    async def load(self, run_id: RunId) -> Result[ReviewTarget, ReviewError]:
        snapshot = self.runs.runs.get(run_id)
        if snapshot is None:
            return Err(ReviewError(ReviewErrorCode.RUN_NOT_FOUND, "Run does not exist."))
        if snapshot.run.state != RunState.AWAITING_REVIEW:
            return Err(
                ReviewError(ReviewErrorCode.RUN_NOT_REVIEWABLE, "Run is not awaiting review.")
            )
        assert snapshot.run.analysis is not None
        return Ok(
            ReviewTarget(
                run_id,
                snapshot.revision,
                tuple(
                    ReviewConclusion(item.id, item.statement)
                    for item in snapshot.run.analysis.conclusions
                ),
            )
        )

    async def commit(
        self, review: Review, *, expected_revision: int
    ) -> Result[Review, ReviewError]:
        snapshot = self.runs.runs.get(review.run_id)
        if (
            snapshot is None
            or snapshot.revision != expected_revision
            or snapshot.run.state != RunState.AWAITING_REVIEW
        ):
            return Err(
                ReviewError(
                    ReviewErrorCode.CONFLICT, "Run is no longer available at this revision."
                )
            )
        if review.id in self.reviews.reviews:
            return Err(ReviewError(ReviewErrorCode.REVIEW_ID_CONFLICT, "Review ID already exists."))
        assert snapshot.run.analysis is not None
        originals = {item.id: item.statement for item in snapshot.run.analysis.conclusions}
        if originals != {
            item.conclusion_id: item.original_statement for item in review.conclusions
        }:
            return Err(
                ReviewError(ReviewErrorCode.CONFLICT, "Review originals do not match the run.")
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
        # Review.submit and the checks above establish the receipt/close invariants.
        assert isinstance(receipt, Ok)
        closed = snapshot.run.close(receipt.value)
        assert isinstance(closed, Ok)
        if self.runs.fail_writes or self.reviews.fail_writes:
            raise RuntimeError("Simulated atomic review storage failure")
        # No suspension or fallible I/O between publishing these two snapshots.
        self.runs.runs[review.run_id] = RunSnapshot(closed.value, snapshot.revision + 1)
        self.reviews.reviews[review.id] = review
        return Ok(review)


@dataclass(frozen=True, slots=True)
class StoredCorrectionLookup:
    runs: InMemoryRunStore
    reviews: InMemoryReviewStorage

    async def get(
        self, review_id: ReviewId, conclusion_id: ConclusionId
    ) -> Result[Correction, EvaluationError]:
        review = self.reviews.reviews.get(review_id)
        if review is None:
            return Err(
                EvaluationError(EvaluationErrorCode.CORRECTION_NOT_FOUND, "Review does not exist.")
            )
        snapshot = self.runs.runs.get(review.run_id)
        if (
            snapshot is None
            or snapshot.run.human_decision is None
            or snapshot.run.human_decision.review_id != review_id
        ):
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
                    snapshot.run.case_id,
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
                EvaluationErrorCode.CORRECTION_NOT_FOUND, "Conclusion is not part of the review."
            )
        )


@dataclass(frozen=True, slots=True)
class MemoryApplication:
    cases: InMemoryCaseRepository
    runs: InMemoryRunStore
    reviews: InMemoryReviewStorage
    references: InMemoryReferenceRepository
    reviewer_access: InMemoryReviewerAccess
    promotion_access: InMemoryPromotionAccess
    review_gateway: ConnectedReviewGateway
    correction_lookup: StoredCorrectionLookup
    open_case: OpenCase
    start_run: StartRun
    record_review: RecordReview
    promote_correction: PromoteCorrection
    queue: InMemoryJobQueue
    journal: MemoryExecutionJournal


def build_memory_application(clock: Clock) -> MemoryApplication:
    cases = InMemoryCaseRepository()
    runs = InMemoryRunStore()
    reviews = InMemoryReviewStorage()
    references = InMemoryReferenceRepository()
    reviewer_access = InMemoryReviewerAccess()
    promotion_access = InMemoryPromotionAccess()
    review_gateway = ConnectedReviewGateway(runs, reviews)
    correction_lookup = StoredCorrectionLookup(runs, reviews)
    queue = InMemoryJobQueue(runs.jobs)
    return MemoryApplication(
        cases,
        runs,
        reviews,
        references,
        reviewer_access,
        promotion_access,
        review_gateway,
        correction_lookup,
        OpenCase(cases, clock),
        StartRun(StoredCaseLookup(cases), runs),
        RecordReview(review_gateway, reviewer_access, clock),
        PromoteCorrection(correction_lookup, references, promotion_access, clock),
        queue,
        MemoryExecutionJournal(runs, queue),
    )
