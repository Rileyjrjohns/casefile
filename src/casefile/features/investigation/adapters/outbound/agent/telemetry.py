"""OpenTelemetry spans and structured logs exclude prompts, responses and credentials."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry.trace import Status, StatusCode, Tracer


class MemoryTelemetry:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.events: list[tuple[str, dict[str, str | int]]] = []

    @contextmanager
    def span(self, name: str, attributes: dict[str, str | int]) -> Iterator[None]:
        self.names.append(name)
        yield

    def event(self, name: str, attributes: dict[str, str | int]) -> None:
        self.events.append((name, attributes.copy()))


class OpenTelemetry:
    def __init__(self, tracer: Tracer, logger: logging.Logger) -> None:
        self.tracer = tracer
        self.logger = logger

    @contextmanager
    def span(self, name: str, attributes: dict[str, str | int]) -> Iterator[None]:
        with self.tracer.start_as_current_span(
            name,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield
            except BaseException as error:
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", type(error).__name__)
                raise

    def event(self, name: str, attributes: dict[str, str | int]) -> None:
        self.logger.info(json.dumps({"event": name, **attributes}, sort_keys=True))
