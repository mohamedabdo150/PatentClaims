"""Structured error contract: {"error": {"code", "message", "requestId", "details"}}.

Public errors never include stack traces, file paths or patent content.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("patent_converter.errors")


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    MISSING_FILE = "MISSING_FILE"
    UNKNOWN_CONFIG_PROFILE = "UNKNOWN_CONFIG_PROFILE"
    PARTIAL_RESULT_NOT_ENABLED = "PARTIAL_RESULT_NOT_ENABLED"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    ENCRYPTED_OR_LEGACY_DOCUMENT = "ENCRYPTED_OR_LEGACY_DOCUMENT"
    INVALID_DOCX = "INVALID_DOCX"
    DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
    CLAIM_EXTRACTION_FAILED = "CLAIM_EXTRACTION_FAILED"
    CLAIM_PAIRING_MISMATCH = "CLAIM_PAIRING_MISMATCH"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    ASSET_CONVERSION_FAILED = "ASSET_CONVERSION_FAILED"
    CONTENT_INTEGRITY_FAILED = "CONTENT_INTEGRITY_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    CONCURRENCY_LIMIT = "CONCURRENCY_LIMIT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INSUFFICIENT_STORAGE = "INSUFFICIENT_STORAGE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"


STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.MISSING_FILE: 400,
    ErrorCode.UNKNOWN_CONFIG_PROFILE: 400,
    ErrorCode.PARTIAL_RESULT_NOT_ENABLED: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UPLOAD_TOO_LARGE: 413,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FILE_TYPE: 415,
    ErrorCode.ENCRYPTED_OR_LEGACY_DOCUMENT: 415,
    ErrorCode.INVALID_DOCX: 422,
    ErrorCode.DOCUMENT_REJECTED: 422,
    ErrorCode.CLAIM_EXTRACTION_FAILED: 422,
    ErrorCode.CLAIM_PAIRING_MISMATCH: 422,
    ErrorCode.OUTPUT_LIMIT_EXCEEDED: 422,
    ErrorCode.UNSUPPORTED_CONTENT: 422,
    ErrorCode.ASSET_CONVERSION_FAILED: 422,
    ErrorCode.CONTENT_INTEGRITY_FAILED: 500,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.CONCURRENCY_LIMIT: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.INSUFFICIENT_STORAGE: 503,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.PROCESSING_TIMEOUT: 504,
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    @property
    def status_code(self) -> int:
        return STATUS[self.code]


def error_body(code: str, message: str, request_id: str | None, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "requestId": request_id, "details": details or {}}}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code.value, exc.message, _request_id(request), exc.details),
                            status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(p) for p in e.get("loc", [])), "problem": e.get("type")} for e in exc.errors()]
        missing = [f["field"] for f in fields if f["problem"] == "missing"]
        code = ErrorCode.MISSING_FILE if any(m.endswith(("englishFile", "arabicFile")) for m in missing) \
            else ErrorCode.INVALID_REQUEST
        return JSONResponse(error_body(code.value, "Invalid request.", _request_id(request), {"fields": fields}),
                            status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(exc.detail), _request_id(request)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(error_body(ErrorCode.INTERNAL_ERROR.value, "Unexpected processing error.",
                                       _request_id(request)), status_code=500)
