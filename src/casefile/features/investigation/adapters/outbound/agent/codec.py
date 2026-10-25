"""Canonical JSON for durable normalized requests, responses and replay matching."""

import json

from pydantic import BaseModel, TypeAdapter

from casefile.features.investigation.adapters.outbound.tools.wire import (
    AlertDocument,
    CaseDocumentRecord,
    KycDocument,
    NoteDocument,
    SanctionDocument,
    TransactionDocument,
)
from casefile.features.investigation.application.agent_types import (
    CaseContext,
    EvidenceBatch,
    ModelRequest,
    ModelResponse,
)
from casefile.features.investigation.application.decisions import DecisionRequest, DecisionResponse
from casefile.features.investigation.application.tools import ToolName


def canonical(payload: str) -> str:
    return json.dumps(
        json.loads(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def encode[T](adapter: TypeAdapter[T], value: T) -> str:
    return canonical(adapter.dump_json(value, warnings="error").decode())


def decode[T](adapter: TypeAdapter[T], payload: str) -> T:
    if len(payload.encode()) > 1_000_000:
        raise ValueError("Agent document exceeds the storage limit.")
    value = adapter.validate_json(payload, strict=True)
    if encode(adapter, value) != canonical(payload):
        raise ValueError("Agent document has unknown, missing or coerced fields.")
    return value


class AgentJsonCodec:
    def decision_request_json(self, value: DecisionRequest) -> str:
        return encode(TypeAdapter(DecisionRequest), value)

    def decision_response_json(self, value: DecisionResponse) -> str:
        return encode(TypeAdapter(DecisionResponse), value)

    def decision_response(self, payload: str) -> DecisionResponse:
        return decode(TypeAdapter(DecisionResponse), payload)

    def context_json(self, value: CaseContext) -> str:
        return encode(TypeAdapter(CaseContext), value)

    def context(self, payload: str) -> CaseContext:
        return decode(TypeAdapter(CaseContext), payload)

    def evidence_json(self, value: EvidenceBatch) -> str:
        return encode(TypeAdapter(EvidenceBatch), value)

    def evidence(self, payload: str) -> EvidenceBatch:
        batch = decode(TypeAdapter(EvidenceBatch), payload)
        documents: dict[ToolName, type[BaseModel]] = {
            ToolName.TRANSACTIONS: TransactionDocument,
            ToolName.KYC: KycDocument,
            ToolName.ALERTS: AlertDocument,
            ToolName.SANCTIONS: SanctionDocument,
            ToolName.NOTES: NoteDocument,
            ToolName.DOCUMENTS: CaseDocumentRecord,
        }
        encoded = json.loads(payload)
        for record in encoded["records"]:
            documents[batch.tool].model_validate(record)
        if len({item.record_id for item in batch.records}) != len(batch.records):
            raise ValueError("Duplicate evidence records.")
        return batch

    def request_json(self, value: ModelRequest) -> str:
        return encode(TypeAdapter(ModelRequest), value)

    def request(self, payload: str) -> ModelRequest:
        request = decode(TypeAdapter(ModelRequest), payload)
        if (
            request.prompt_version != "casefile-narrative-v1"
            or not 1 <= request.max_output_tokens <= 4096
        ):
            raise ValueError("Unsupported narrative request.")
        for batch in request.evidence:
            self.evidence(self.evidence_json(batch))
        return request

    def response_json(self, value: ModelResponse) -> str:
        return encode(TypeAdapter(ModelResponse), value)

    def response(self, payload: str) -> ModelResponse:
        response = decode(TypeAdapter(ModelResponse), payload)
        if (
            response.input_tokens < 0
            or response.output_tokens < 0
            or not response.estimated_cost.is_finite()
            or response.estimated_cost < 0
            or not response.model.strip()
        ):
            raise ValueError("Invalid model usage or identity.")
        return response
