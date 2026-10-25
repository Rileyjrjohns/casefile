"""Thin HTTP routes over the same commands and queries exposed through MCP."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import JsonValue
from starlette.middleware.trustedhost import TrustedHostMiddleware

from casefile.bootstrap.runtime import Runtime, TransportFailure
from casefile.features.case_intake.adapters.inbound.documents import OpenCaseDocument
from casefile.features.evaluation.adapters.inbound.documents import PromoteReferenceDocument
from casefile.features.investigation.adapters.inbound.documents import StartRunDocument
from casefile.features.review.adapters.inbound.documents import ReviewDocument


async def work(runtime: Runtime) -> None:
    while True:
        try:
            if not await runtime.worker.run_once():
                await asyncio.sleep(0.2)
        except Exception as error:
            logging.getLogger("casefile.worker").error(
                "Worker attempt failed: %s", type(error).__name__
            )
            await asyncio.sleep(1)


def create_app(
    runtime: Runtime, *, background_worker: bool = True, access_token: str | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(work(runtime)) if background_worker else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Casefile", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            access_token
            and request.url.path != "/health"
            and not secrets.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {access_token}"
            )
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"error": "untrusted_origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(TransportFailure)
    async def failure(request: Request, error: TransportFailure) -> JSONResponse:
        return JSONResponse(
            {"error": error.code, "message": error.message}, status_code=error.status
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/identity")
    async def identity() -> dict[str, JsonValue]:
        return {"reviewer_id": str(runtime.reviewer), "demo": True}

    @app.get("/cases")
    async def cases(
        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
    ) -> dict[str, JsonValue]:
        return await runtime.list_cases(limit, offset)

    @app.post("/cases", status_code=201)
    async def open_case(payload: OpenCaseDocument) -> dict[str, JsonValue]:
        return await runtime.open_case(payload)

    @app.get("/cases/{case_id}")
    async def get_case(case_id: UUID) -> dict[str, JsonValue]:
        return await runtime.get_case(case_id)

    @app.get("/cases/{case_id}/runs")
    async def case_runs(case_id: UUID) -> dict[str, JsonValue]:
        return await runtime.case_runs(case_id)

    @app.post("/cases/{case_id}/runs", status_code=202)
    async def start_run(case_id: UUID, payload: StartRunDocument) -> dict[str, JsonValue]:
        return await runtime.start_run(case_id, payload)

    @app.get("/runs/{run_id}")
    async def get_run(run_id: UUID) -> dict[str, JsonValue]:
        return await runtime.get_run(run_id)

    @app.get("/runs/{run_id}/sources/{tool}/{record_id}")
    async def source(run_id: UUID, tool: str, record_id: str) -> dict[str, JsonValue]:
        return await runtime.source(run_id, tool, record_id)

    @app.post("/runs/{run_id}/review")
    async def review(run_id: UUID, payload: ReviewDocument) -> dict[str, JsonValue]:
        return await runtime.review(run_id, payload)

    @app.post("/runs/{run_id}/conclusions/{conclusion_id}/reference")
    async def promote_reference(
        run_id: UUID, conclusion_id: UUID, payload: PromoteReferenceDocument
    ) -> dict[str, JsonValue]:
        return await runtime.promote_reference(run_id, conclusion_id, payload)

    @app.get("/references/{reference_id}")
    async def reference_bundle(reference_id: UUID) -> JSONResponse:
        return JSONResponse(
            await runtime.reference_bundle(reference_id),
            headers={
                "Content-Disposition": (
                    f'attachment; filename="casefile-reference-{reference_id}.json"'
                )
            },
        )

    return app
