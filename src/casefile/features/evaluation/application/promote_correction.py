from dataclasses import dataclass

from casefile.features.evaluation.application.ports import (
    CorrectionLookup,
    PromotionAccess,
    ReferenceRepository,
)
from casefile.features.evaluation.domain.errors import EvaluationError, EvaluationErrorCode
from casefile.features.evaluation.domain.reference import ReferenceExample
from casefile.shared.clock import Clock
from casefile.shared.ids import ConclusionId, ReferenceId, ReviewerId, ReviewId
from casefile.shared.result import Err, Result


@dataclass(frozen=True, slots=True)
class PromoteCorrectionCommand:
    reference_id: ReferenceId
    review_id: ReviewId
    conclusion_id: ConclusionId
    actor_id: ReviewerId


@dataclass(frozen=True, slots=True)
class PromoteCorrection:
    corrections: CorrectionLookup
    repository: ReferenceRepository
    access: PromotionAccess
    clock: Clock

    async def execute(
        self, command: PromoteCorrectionCommand
    ) -> Result[ReferenceExample, EvaluationError]:
        if not await self.access.can_promote(command.actor_id):
            return Err(
                EvaluationError(
                    EvaluationErrorCode.ACCESS_DENIED, "Actor cannot promote corrections."
                )
            )
        correction = await self.corrections.get(command.review_id, command.conclusion_id)
        if isinstance(correction, Err):
            return correction
        example = ReferenceExample.create(
            command.reference_id, correction.value, command.actor_id, self.clock.now()
        )
        if isinstance(example, Err):
            return example
        return await self.repository.add_once(example.value)
