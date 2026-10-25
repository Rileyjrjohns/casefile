from typing import Protocol

from casefile.features.evaluation.domain.errors import EvaluationError
from casefile.features.evaluation.domain.reference import Correction, ReferenceExample
from casefile.shared.ids import ConclusionId, ReferenceId, ReviewerId, ReviewId
from casefile.shared.result import Result


class CorrectionLookup(Protocol):
    async def get(
        self, review_id: ReviewId, conclusion_id: ConclusionId
    ) -> Result[Correction, EvaluationError]:
        """Return a correction from a persisted, committed human review."""
        ...


class PromotionAccess(Protocol):
    async def can_promote(self, actor_id: ReviewerId) -> bool: ...


class ReferenceRepository(Protocol):
    async def get(self, reference_id: ReferenceId) -> ReferenceExample | None: ...

    async def add_once(
        self, example: ReferenceExample
    ) -> Result[ReferenceExample, EvaluationError]:
        """Insert once per (review, conclusion); return the existing promotion on retry."""
        ...
