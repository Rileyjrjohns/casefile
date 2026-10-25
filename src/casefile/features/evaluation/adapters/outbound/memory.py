from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.features.evaluation.domain.reference import Correction, ReferenceExample
from casefile.shared.ids import ConclusionId, ReferenceId, ReviewerId, ReviewId
from casefile.shared.result import Err, Ok, Result


class InMemoryCorrectionLookup:
    def __init__(
        self, corrections: dict[tuple[ReviewId, ConclusionId], Correction] | None = None
    ) -> None:
        self.corrections = dict(corrections or {})

    async def get(
        self, review_id: ReviewId, conclusion_id: ConclusionId
    ) -> Result[Correction, EvaluationError]:
        correction = self.corrections.get((review_id, conclusion_id))
        if correction is None:
            return Err(
                EvaluationError(
                    EvaluationErrorCode.CORRECTION_NOT_FOUND, "Persisted correction does not exist."
                )
            )
        return Ok(correction)


class InMemoryPromotionAccess:
    def __init__(self, allowed: set[ReviewerId] | None = None) -> None:
        self.allowed = set(allowed or ())

    async def can_promote(self, actor_id: ReviewerId) -> bool:
        return actor_id in self.allowed


class InMemoryReferenceRepository:
    def __init__(self) -> None:
        self.examples: dict[ReferenceId, ReferenceExample] = {}
        self.fail_writes = False

    async def get(self, reference_id: ReferenceId) -> ReferenceExample | None:
        return self.examples.get(reference_id)

    async def add_once(
        self, example: ReferenceExample
    ) -> Result[ReferenceExample, EvaluationError]:
        key = (example.correction.review_id, example.correction.conclusion_id)
        for existing in self.examples.values():
            if (existing.correction.review_id, existing.correction.conclusion_id) == key:
                return Ok(existing)
        if example.id in self.examples:
            return Err(
                EvaluationError(
                    EvaluationErrorCode.REFERENCE_ID_CONFLICT,
                    "Reference ID already belongs to another correction.",
                )
            )
        if self.fail_writes:
            raise RuntimeError("Simulated reference storage failure")
        self.examples[example.id] = example
        return Ok(example)
