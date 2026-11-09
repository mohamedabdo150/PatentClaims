"""Shared application state and request dependencies (auth, rate limiting)."""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from fastapi import Request

from typing import Any

from ..config.claim_patterns import PatternRegistry
from ..pdf.drawing_detection import DrawingModel
from ..config.settings import Settings
from ..security.resource_limits import JobLimiter, RateLimiter
from .errors import AppError, ErrorCode

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


@dataclass
class AppState:
    settings: Settings
    patterns: PatternRegistry
    limiter: JobLimiter
    rate_limiter: RateLimiter
    executor: ThreadPoolExecutor
    diagnostics: "DiagnosticsStore"
    drawing_model: Any = None

    @classmethod
    def create(cls, settings: Settings) -> "AppState":
        slots = settings.processing.max_concurrent_jobs_per_worker
        return cls(
            settings=settings,
            patterns=PatternRegistry.from_file(settings.claim_patterns_file),
            limiter=JobLimiter(slots),
            rate_limiter=RateLimiter(settings.security.rate_limit_requests_per_minute),
            # +1 so a timed-out job finishing its cleanup never blocks a new one from being scheduled.
            executor=ThreadPoolExecutor(max_workers=slots + 1, thread_name_prefix="job"),
            diagnostics=DiagnosticsStore(200),
            drawing_model=DrawingModel(settings.pdf),
        )


class DiagnosticsStore:
    """Bounded in-memory store of recent conversion diagnostics (counts only, never patent content)."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, request_id: str, data: dict) -> None:
        with self._lock:
            self._items[request_id] = data
            self._items.move_to_end(request_id)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)

    def get(self, request_id: str) -> dict | None:
        with self._lock:
            return self._items.get(request_id)


def get_state(request: Request) -> AppState:
    return request.app.state.app_state


def resolve_request_id(candidate: str | None) -> str:
    if candidate is None or candidate == "":
        return uuid.uuid4().hex
    if not REQUEST_ID_RE.match(candidate):
        raise AppError(ErrorCode.INVALID_REQUEST, "requestId must match [A-Za-z0-9._-]{1,64}.")
    return candidate


def authenticate(request: Request) -> str:
    """Return a client identity for rate limiting; raise 401 when authentication fails.

    Integration point: replace/extend with OAuth2/JWT validation if the
    OutSystems environment uses an identity provider.
    """
    state = get_state(request)
    sec = state.settings.security
    client = request.client.host if request.client else "unknown"
    if not sec.auth_enabled:
        identity = f"ip:{client}"
    else:
        key = request.headers.get(sec.api_key_header)
        if not key:
            raise AppError(ErrorCode.UNAUTHORIZED, "Missing API key.", headers={"WWW-Authenticate": "ApiKey"})
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if not any(hmac.compare_digest(digest, allowed) for allowed in sec.api_key_sha256):
            raise AppError(ErrorCode.UNAUTHORIZED, "Invalid API key.", headers={"WWW-Authenticate": "ApiKey"})
        identity = f"key:{digest[:16]}"
    if not state.rate_limiter.allow(identity):
        raise AppError(ErrorCode.RATE_LIMITED, "Too many requests.", headers={"Retry-After": "60"})
    return identity
