"""HTTP endpoints."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .. import __version__
from ..diagnostics import metrics
from ..pipeline.jobs import UploadedDocument, run_job
from ..security.resource_limits import Deadline
from ..storage.temporary_storage import JOBS_DIR, directory_size, free_disk_bytes
from .dependencies import AppState, authenticate, get_state, resolve_request_id
from .errors import AppError, ErrorCode
from .schemas import ConvertResponse, ErrorResponse

router = APIRouter()
_ERRORS = {code: {"model": ErrorResponse} for code in (400, 401, 404, 413, 415, 422, 429, 500, 503, 504)}


def _parse_bool(value: str | None, name: str) -> bool:
    if value is None or value == "":
        return False
    v = value.strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise AppError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean.")


async def _execute(
    request: Request,
    state: AppState,
    mode: str,
    english: UploadFile,
    arabic: UploadFile,
    config_profile: str | None,
    request_id: str | None,
    strict_validation: str | None,
    allow_partial: str | None,
) -> dict[str, Any]:
    settings = state.settings
    rid = resolve_request_id(request_id or request.state.request_id)
    request.state.request_id = rid
    strict = _parse_bool(strict_validation, "strictValidation")
    if _parse_bool(allow_partial, "allowPartialResult"):
        raise AppError(ErrorCode.PARTIAL_RESULT_NOT_ENABLED,
                       "Partial results are disabled; conversion is all-or-nothing.")
    profile_name = config_profile or settings.default_config_profile
    try:
        profile = state.patterns.get(profile_name)
    except KeyError:
        raise AppError(ErrorCode.UNKNOWN_CONFIG_PROFILE, "Unknown configProfile.",
                       {"available": state.patterns.names()}) from None

    if not state.limiter.try_acquire():
        raise AppError(ErrorCode.CONCURRENCY_LIMIT, "Worker is busy with another conversion.",
                       headers={"Retry-After": "30"})
    metrics.ACTIVE_JOBS.inc()

    def on_finish() -> None:
        metrics.ACTIVE_JOBS.dec()
        state.limiter.release()

    p = settings.processing
    uploads = [
        UploadedDocument("englishFile", "en", english.file, english.filename, english.content_type, p.max_english_bytes),
        UploadedDocument("arabicFile", "ar", arabic.file, arabic.filename, arabic.content_type, p.max_arabic_bytes),
    ]
    deadline = Deadline(p.max_processing_seconds)
    try:
        future = state.executor.submit(run_job, settings, profile, rid, uploads, deadline, mode, on_finish, strict)
    except Exception:
        on_finish()
        raise
    try:
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=p.max_processing_seconds + 5)
    except asyncio.TimeoutError:
        deadline.cancel()  # the worker thread aborts at its next checkpoint and cleans up
        raise AppError(ErrorCode.PROCESSING_TIMEOUT, "Processing exceeded the configured time limit.") from None


def _observe(endpoint: str, t0: float, outcome: str, code: str | None = None) -> None:
    metrics.REQUESTS.labels(endpoint, outcome).inc()
    metrics.DURATION.labels(endpoint).observe(time.perf_counter() - t0)
    if code:
        metrics.FAILURES.labels(code).inc()


@router.post(
    "/api/v1/patent/claims/convert",
    response_model=ConvertResponse,
    responses=_ERRORS,
    summary="Convert an English + Arabic patent DOCX pair into paired claim HTML",
)
async def convert(
    request: Request,
    englishFile: UploadFile = File(...),
    arabicFile: UploadFile = File(...),
    configProfile: str | None = Form(None),
    requestId: str | None = Form(None),
    strictValidation: str | None = Form(None),
    allowPartialResult: str | None = Form(None),
    _identity: str = Depends(authenticate),
    state: AppState = Depends(get_state),
) -> Any:
    t0 = time.perf_counter()
    try:
        result = await _execute(request, state, "convert", englishFile, arabicFile, configProfile, requestId,
                                strictValidation, allowPartialResult)
    except AppError as exc:
        _observe("convert", t0, "error", exc.code.value)
        raise
    _observe("convert", t0, "success")
    diag = result["diagnostics"]
    state.diagnostics.put(diag["requestId"], diag)
    path = result["responsePath"]
    en, ar = diag["english"], diag["arabic"]
    headers = {
        "X-Claims-Count": str(diag["claimCount"]),
        "X-Response-Bytes": str(diag["responseBytes"]),
        "X-Processing-Seconds": str(diag["processingSeconds"]),
        "X-Images-Embedded": str(en["imagesEmbedded"] + ar["imagesEmbedded"]),
        "X-Equations-Rendered": str(en["equationsMathml"] + en["equationsImage"] + ar["equationsMathml"]
                                    + ar["equationsImage"]),
        "X-Tables-Rendered": str(en["tablesRendered"] + ar["tablesRendered"]),
        "X-Warnings-Count": str(sum(en["warnings"].values()) + sum(ar["warnings"].values())),
    }
    return FileResponse(path, media_type="application/json", headers=headers,
                        background=BackgroundTask(_remove, path))


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@router.get(
    "/api/v1/patent/claims/diagnostics/{request_id}",
    responses=_ERRORS,
    summary="Diagnostics (counts, timings, warnings; no patent content) of a recent conversion on this worker",
)
async def diagnostics(request_id: str, _identity: str = Depends(authenticate),
                      state: AppState = Depends(get_state)) -> Any:
    data = state.diagnostics.get(request_id)
    if data is None:
        raise AppError(ErrorCode.NOT_FOUND, "No diagnostics for this requestId on this worker.")
    return JSONResponse(data)


@router.post(
    "/api/v1/patent/claims/analyze",
    responses=_ERRORS,
    summary="Diagnostic: DOCX capability analysis of an English + Arabic pair (no patent text returned)",
)
async def analyze(
    request: Request,
    englishFile: UploadFile = File(...),
    arabicFile: UploadFile = File(...),
    configProfile: str | None = Form(None),
    requestId: str | None = Form(None),
    _identity: str = Depends(authenticate),
    state: AppState = Depends(get_state),
) -> Any:
    t0 = time.perf_counter()
    try:
        result = await _execute(request, state, "analyze", englishFile, arabicFile, configProfile, requestId,
                                None, None)
    except AppError as exc:
        _observe("analyze", t0, "error", exc.code.value)
        raise
    _observe("analyze", t0, "success")
    result["requestId"] = request.state.request_id
    return JSONResponse(result)


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", summary="Readiness probe (storage, configuration, free job slot)")
async def ready(state: AppState = Depends(get_state)) -> JSONResponse:
    st = state.settings.storage
    checks: dict[str, Any] = {}
    try:
        root = st.temporary_directory
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".ready-{uuid.uuid4().hex}"
        probe.write_bytes(b"ok")
        probe.unlink()
        checks["storageWritable"] = True
        free = free_disk_bytes(root)
        checks["storageFreeBytes"] = free
        checks["storageFreeOk"] = free >= st.min_free_disk_gb * 1024**3
    except OSError:
        checks["storageWritable"] = False
        checks["storageFreeOk"] = False
    checks["patternProfiles"] = state.patterns.names()
    checks["activeJobs"] = state.limiter.active
    checks["jobSlotAvailable"] = state.limiter.active < state.limiter.slots
    ok = checks["storageWritable"] and checks["storageFreeOk"] and checks["jobSlotAvailable"]
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", summary="Prometheus metrics")
async def metrics_endpoint(state: AppState = Depends(get_state)) -> Response:
    root = state.settings.storage.temporary_directory
    try:
        metrics.TEMP_FREE.set(free_disk_bytes(root))
        jobs = root / JOBS_DIR
        metrics.TEMP_USED.set(directory_size(jobs) if jobs.is_dir() else 0)
    except OSError:
        pass
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


__all__ = ["router"]
