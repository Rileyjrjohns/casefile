"""Explicit tracing configuration; no remote exporter is enabled by default."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from casefile.features.investigation.adapters.outbound.agent.telemetry import OpenTelemetry


@contextmanager
def configured_telemetry(env: Mapping[str, str]) -> Iterator[OpenTelemetry]:
    provider = TracerProvider(resource=Resource.create({"service.name": "casefile"}))
    endpoint = env.get("CASEFILE_OTLP_TRACES_ENDPOINT")
    if endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5))
        )
    logger = logging.Logger("casefile.agent", level=logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        yield OpenTelemetry(provider.get_tracer("casefile.investigation", "0.1.0"), logger)
    finally:
        provider.shutdown()
        handler.close()
