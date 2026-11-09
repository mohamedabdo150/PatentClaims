"""Routes of the previous service (patent_claims / AI_API_v2.py), same names and formats.

    POST /word_to_json          multipart english_file / arabic_file (each optional, at least one)
                                -> {"claims":[{"id":1,"claim_english":"…","claim_arabic":"…"}]}
    POST /convert_word_to_html  raw DOCX body -> {"success":true,"html":"…"} (whole document)
    POST /drawing_detection     raw PDF body  -> {"results":[{"page":1,"result":"Valid drawing"}]}
    POST /pdf-to-images         raw PDF body  -> {"success":true,"images":["<base64>",…]}
    GET  /health                (api/routes.py) -> {"status":"OK",…}

Behaviour kept from the previous service: no API key by default, UTF-8 JSON, a request
waits for a free worker instead of being rejected, single-language uploads allowed.
Deliberate differences: claims are paired by claim number (``id`` = claim number), and
a conversion is all-or-nothing (no partial result when one document fails).
Errors keep the previous keys (``detail``, ``error``, ``message``); see api/errors.py.
"""

from __future__ import annotations

import tempfile
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from ..config.settings import MB
from ..diagnostics import metrics
from ..pdf.drawing_detection import detect_drawings
from ..pdf.pdf_images import extract_images
from ..pipeline.jobs import UploadedDocument
from ..security.resource_limits import Deadline, DeadlineExceeded
from .dependencies import AppState, authenticate, get_state, resolve_request_id
from .errors import LEGACY_PATHS, AppError, ErrorCode  # noqa: F401  (LEGACY_PATHS re-exported)
from .routes import _observe, _remove, acquire_slot, release_slot, run_in_worker, submit_job

router = APIRouter()
JSON_UTF8 = "application/json; charset=utf-8"
CHUNK = 1024 * 1024


def legacy_identity(request: Request) -> str:
    state: AppState = request.app.state.app_state
    if state.settings.legacy.require_api_key:
        return authenticate(request)
    client = request.client.host if request.client else "unknown"
    identity = f"ip:{client}"
    if not state.rate_limiter.allow(identity):
        raise AppError(ErrorCode.RATE_LIMITED, "Too many requests.", headers={"Retry-After": "60"})
    return identity


async def _read_body_to_spool(request: Request, limit: int) -> tempfile.SpooledTemporaryFile:
    spool = tempfile.SpooledTemporaryFile(max_size=CHUNK)
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            spool.close()
            raise AppError(ErrorCode.FILE_TOO_LARGE, "Request body exceeds the maximum allowed size.",
                           {"maxBytes": limit})
        spool.write(chunk)
    spool.seek(0)
    if size == 0:
        spool.close()
        raise AppError(ErrorCode.MISSING_FILE, "Request body is empty.")
    return spool


def _present(f: UploadFile | None) -> bool:
    return f is not None and (f.size is None or f.size > 0)


def _file_response(result: dict[str, Any]) -> FileResponse:
    path = result["responsePath"]
    return FileResponse(path, media_type=JSON_UTF8, background=BackgroundTask(_remove, path))


@router.post("/word_to_json", summary="Previous-service claims API (english_file / arabic_file)")
async def word_to_json(
    request: Request,
    english_file: UploadFile | None = File(None),
    arabic_file: UploadFile | None = File(None),
    _identity: str = Depends(legacy_identity),
    state: AppState = Depends(get_state),
) -> Any:
    t0 = time.perf_counter()
    try:
        if not _present(english_file) and not _present(arabic_file):
            raise AppError(ErrorCode.MISSING_FILE, "At least one file must be provided")
        p = state.settings.processing
        uploads = []
        if _present(english_file):
            uploads.append(UploadedDocument("english_file", "en", english_file.file, english_file.filename,
                                            None, p.max_english_bytes))
        if _present(arabic_file):
            uploads.append(UploadedDocument("arabic_file", "ar", arabic_file.file, arabic_file.filename,
                                            None, p.max_arabic_bytes))
        rid = resolve_request_id(request.state.request_id)
        profile = state.patterns.get(state.settings.default_config_profile)
        result = await submit_job(state, rid, "legacy_claims", uploads, profile, False, wait_for_slot=True)
    except AppError as exc:
        _observe("word_to_json", t0, "error", exc.code.value)
        raise
    _observe("word_to_json", t0, "success")
    state.diagnostics.put(result["diagnostics"]["requestId"], result["diagnostics"])
    return _file_response(result)


@router.post("/convert_word_to_html", summary="Previous-service API: whole DOCX (raw body) to HTML")
async def convert_word_to_html(
    request: Request, _identity: str = Depends(legacy_identity), state: AppState = Depends(get_state),
) -> Any:
    t0 = time.perf_counter()
    try:
        p = state.settings.processing
        body = await _read_body_to_spool(request, p.max_english_bytes)
        rid = resolve_request_id(request.state.request_id)
        profile = state.patterns.get(state.settings.default_config_profile)
        uploads = [UploadedDocument("body", "doc", body, None, None, p.max_english_bytes)]
        try:
            result = await submit_job(state, rid, "document", uploads, profile, False, wait_for_slot=True)
        finally:
            body.close()
    except AppError as exc:
        _observe("convert_word_to_html", t0, "error", exc.code.value)
        raise
    _observe("convert_word_to_html", t0, "success")
    state.diagnostics.put(result["diagnostics"]["requestId"], result["diagnostics"])
    return _file_response(result)


async def _pdf_job(request: Request, state: AppState, name: str, work) -> Any:
    t0 = time.perf_counter()
    cfg = state.settings.pdf
    try:
        if not cfg.enabled:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "PDF routes are disabled.")
        body = await _read_body_to_spool(request, int(cfg.max_pdf_mb * MB))
        data = body.read()
        body.close()
        await acquire_slot(state, state.settings.processing.queue_wait_seconds)
        deadline = Deadline(cfg.timeout_seconds)

        def job() -> Any:
            try:
                return work(data, deadline.check)
            except DeadlineExceeded:
                raise AppError(ErrorCode.PROCESSING_TIMEOUT, "Processing exceeded the configured time limit.") from None
            finally:
                release_slot(state)

        result = await run_in_worker(state, job, cfg.timeout_seconds, deadline)
    except AppError as exc:
        _observe(name, t0, "error", exc.code.value)
        raise
    _observe(name, t0, "success")
    return JSONResponse(result, media_type=JSON_UTF8)


@router.post("/drawing_detection", summary="Previous-service API: classify PDF pages (raw PDF body)")
async def drawing_detection(request: Request, _identity: str = Depends(legacy_identity),
                            state: AppState = Depends(get_state)) -> Any:
    model = state.drawing_model
    return await _pdf_job(request, state, "drawing_detection",
                          lambda data, check: {"results": detect_drawings(data, model, check)})


@router.post("/pdf-to-images", summary="Previous-service API: embedded images of a PDF (raw PDF body)")
async def pdf_to_images(request: Request, _identity: str = Depends(legacy_identity),
                        state: AppState = Depends(get_state)) -> Any:
    cfg = state.settings.pdf
    return await _pdf_job(request, state, "pdf_to_images",
                          lambda data, check: {"success": True, "images": extract_images(data, cfg, check)})
