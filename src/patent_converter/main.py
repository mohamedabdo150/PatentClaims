"""FastAPI application factory.

Run with: ``uvicorn patent_converter.main:create_app --factory``
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from . import __version__
from .api.dependencies import REQUEST_ID_RE, AppState
from .api.errors import install_error_handlers
from .api.legacy_routes import LEGACY_PATHS
from .api.legacy_routes import router as legacy_router
from .api.routes import router
from .config.settings import Settings, get_settings
from .diagnostics.logging_config import configure_logging, request_id_var
from .security.resource_limits import BodySizeLimitMiddleware
from .storage.temporary_storage import sweep_stale_jobs

log = logging.getLogger("patent_converter")
MULTIPART_OVERHEAD = 1024 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.logging.level, settings.logging.json_format)
    state = AppState.create(settings)  # validates and compiles claim patterns (fail fast)
    settings.storage.temporary_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only directories older than the retention window are removed: other workers sharing the
    # volume may have jobs in flight. Retention must exceed max_processing_seconds.
    retention = settings.storage.stale_job_retention_minutes
    if retention * 60 <= settings.processing.max_processing_seconds:
        raise ValueError("storage.stale_job_retention_minutes must exceed processing.max_processing_seconds")
    removed = sweep_stale_jobs(settings.storage.temporary_directory, retention)
    log.info("startup", extra={"version": __version__, "environment": settings.environment,
                               "profiles": state.patterns.names(), "staleJobsRemoved": removed})

    async def _periodic_sweep() -> None:
        while True:
            await asyncio.sleep(600)
            await asyncio.to_thread(sweep_stale_jobs, settings.storage.temporary_directory, retention)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_periodic_sweep())
        try:
            yield
        finally:
            task.cancel()
            state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Patent Claims Converter", version=__version__, lifespan=lifespan,
                  docs_url="/docs" if settings.environment != "production" else None, redoc_url=None)
    app.state.app_state = state
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        header_id = request.headers.get("X-Request-ID")
        rid = header_id if header_id and REQUEST_ID_RE.match(header_id) else uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.include_router(router)
    if settings.legacy.enabled:
        app.include_router(legacy_router)
    state.drawing_model.load_async()
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.processing.max_combined_bytes + MULTIPART_OVERHEAD,
        path_prefixes=("/api/", *sorted(LEGACY_PATHS)),
    )

    return app
