import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from casefile.bootstrap.agent import build_agent_worker
from casefile.features.investigation.adapters.outbound.agent.models import FakeLanguageModel
from casefile.features.investigation.adapters.outbound.agent.telemetry import OpenTelemetry
from casefile.features.investigation.adapters.outbound.tools.memory import MemoryInvestigationTools
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from tests.contracts.support import FixedClock
from tests.test_agent_workflow import create_run


class CapturedLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_run_tool_and_model_spans_share_one_trace_without_evidence_text() -> None:
    app = await create_run()
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    logger = logging.Logger("casefile.agent.test", level=logging.INFO)
    captured = CapturedLogs()
    logger.addHandler(captured)
    telemetry = OpenTelemetry(provider.get_tracer("casefile"), logger)
    await build_agent_worker(
        app, MemoryInvestigationTools(load_seed()), FakeLanguageModel(), FixedClock(), telemetry
    ).run_once()
    spans = exporter.get_finished_spans()
    assert sum(not span.name.startswith("decision.") for span in spans) == 7
    assert any(span.name.startswith("decision.") for span in spans)
    root = next(span for span in spans if span.name == "investigation.run")
    assert root.context is not None
    assert all(
        span.context is not None and span.context.trace_id == root.context.trace_id
        for span in spans
    )
    assert all(
        span.parent and span.parent.span_id == root.context.span_id
        for span in spans
        if span is not root
    )
    serialized = " ".join(span.to_json() for span in spans) + " ".join(captured.messages)
    assert "Demo Cobalt" not in serialized and "Ignore previous instructions" not in serialized
    assert "AWAITING_REVIEW" in serialized
    with pytest.raises(RuntimeError), telemetry.span("failed.call", {}):
        raise RuntimeError("test-secret")
    failed = exporter.get_finished_spans()[-1]
    assert "test-secret" not in failed.to_json()
    assert failed.attributes is not None and failed.attributes["error.type"] == "RuntimeError"
    provider.shutdown()
