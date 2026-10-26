"""The replay file format, and the transport that captures one from a live provider."""

import json
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from casefile.shared.result import Err, Ok, Result

_OBJECT = TypeAdapter(dict[str, JsonValue])


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


class Capture(httpx.AsyncByteStream):
    """Passes a response body through unchanged and reports it once the reader has taken all of it.

    A reader that stops early (for example at a size cap) never completes the stream, so a
    truncated body is never reported.
    """

    def __init__(self, stream: httpx.AsyncByteStream, done: Callable[[bytes], None]) -> None:
        self.stream = stream
        self.done = done

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raw = bytearray()
        async for chunk in self.stream:
            raw.extend(chunk)
            yield chunk
        self.done(bytes(raw))

    async def aclose(self) -> None:
        await self.stream.aclose()


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forwards every request to the real transport and keeps what each provider answered.

    A successful (2xx) response whose body is a JSON object is stored under the canonical form of
    the request body, per provider. A provider is recognised by its endpoint URL. Headers, the
    credential and the URL never enter `responses`, which holds only request and response bodies;
    when a question repeats, the first answer is kept so a replay is deterministic.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, urls: Mapping[str, str]) -> None:
        self.inner = inner
        self.names = {httpx.URL(url): name for name, url in urls.items()}
        self.responses: dict[str, dict[str, dict[str, JsonValue]]] = {name: {} for name in urls}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        name = self.names.get(request.url)
        if name is None or not response.is_success:
            return response
        if not isinstance(response.stream, httpx.AsyncByteStream):
            raise TypeError("A recorded provider response must be an asynchronous stream.")
        key = canonical(json.loads(request.content))
        headers = response.headers
        status_code = response.status_code

        def keep(raw: bytes) -> None:
            try:
                body = _OBJECT.validate_json(
                    httpx.Response(status_code, headers=headers, content=raw).content
                )
            except ValidationError:
                return
            self.responses[name].setdefault(key, body)

        return httpx.Response(
            status_code,
            headers=headers,
            stream=Capture(response.stream, keep),
            extensions=response.extensions,
        )

    async def __aenter__(self) -> Self:
        await self.inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.inner.__aexit__(exc_type, exc_value, traceback)


def write_recording(
    path: Path, recording: Recording, secrets: tuple[str, ...]
) -> Result[None, str]:
    """Replace `path` with the recording in one step, unless it contains a credential.

    The file is staged next to its destination and renamed over it, so an interrupted write leaves
    the previous file whole and a symbolic link is replaced instead of written through.
    """
    text = recording.model_dump_json(indent=2, exclude_none=True) + "\n"
    if any(secret and secret in text for secret in secrets):
        return Err("a provider response contains the API key")
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    try:
        staged.write_text(text, encoding="utf-8")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return Ok(None)
