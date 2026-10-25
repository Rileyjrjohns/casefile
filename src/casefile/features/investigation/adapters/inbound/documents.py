from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class StartRunDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    tool_calls: Annotated[int, Field(ge=0, le=20, strict=True)] = 5
    tokens: Annotated[int, Field(ge=0, le=100_000, strict=True)] = 100_000
    estimated_cost: Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)] = Decimal("1")
