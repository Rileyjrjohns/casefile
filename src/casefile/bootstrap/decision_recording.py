"""The replay file format shared by the decision benchmark and the recorder."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


def canonical(body: object) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    provenance: Literal["synthetic-protocol-fixture", "provider-recording"]
    responses: dict[str, dict[str, JsonValue]]
