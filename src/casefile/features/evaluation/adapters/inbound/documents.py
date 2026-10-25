"""Explicit local curation request; the source correction is resolved server-side."""

from pydantic import BaseModel, ConfigDict


class PromoteReferenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    confirmed: bool = False
