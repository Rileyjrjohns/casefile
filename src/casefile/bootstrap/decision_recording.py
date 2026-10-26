"""The replay file format shared by the decision benchmark and the recorder."""

import json
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, model_validator


def canonical(body: object) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class Recording(BaseModel):
    """Frozen provider answers, keyed by the canonical request body that produced them.

    Version 1 files are synthetic protocol fixtures with no provider identity. Version 2 files are
    captured from a live provider and add the model name sent in every request, so a replay can
    send the same name and match the keys, and the instant of capture. Nothing else about the
    exchange is stored: no header, credential or URL.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1, 2]
    provenance: Literal["synthetic-protocol-fixture", "provider-recording"]
    model: str | None = None
    recorded_at: AwareDatetime | None = None
    responses: dict[str, dict[str, JsonValue]]

    @model_validator(mode="after")
    def _identity_matches_provenance(self) -> Self:
        if self.provenance == "provider-recording":
            if self.schema_version != 2 or not self.model or not self.model.strip():
                raise ValueError("A provider recording is schema version 2 and names its model.")
            if self.recorded_at is None:
                raise ValueError("A provider recording states when it was recorded.")
        elif self.schema_version != 1 or self.model is not None or self.recorded_at is not None:
            raise ValueError("A synthetic fixture is schema version 1 with no provider identity.")
        return self
