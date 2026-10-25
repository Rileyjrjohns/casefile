from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from casefile.features.case_intake.application.open_case import OpenCaseCommand
from casefile.features.case_intake.domain.case import AlertKind
from casefile.shared.ids import CaseId


class OpenCaseDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID = Field(default_factory=uuid4)
    alert_id: Annotated[str, Field(min_length=1, max_length=200)]
    subject_id: Annotated[str, Field(pattern=r"^demo-customer-[0-9]{3}$")]
    kind: AlertKind
    description: Annotated[str, Field(min_length=1, max_length=2000)]

    def command(self) -> OpenCaseCommand:
        return OpenCaseCommand(
            CaseId(self.id), self.alert_id, self.subject_id, self.kind, self.description
        )
