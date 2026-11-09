"""Concurrency limits, rate limiting, deadlines and request-body size enforcement."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


class DeadlineExceeded(Exception):
    pass


class Deadline:
    """Cooperative deadline checked inside long-running processing loops."""

    def __init__(self, seconds: float | None):
        self.expires_at = None if seconds is None else time.monotonic() + seconds
        self.cancelled = threading.Event()

    def check(self) -> None:
        if self.cancelled.is_set() or (self.expires_at is not None and time.monotonic() > self.expires_at):
            raise DeadlineExceeded()

    def cancel(self) -> None:
        self.cancelled.set()


class JobLimiter:
    """Non-blocking per-worker job slots. Released by the worker thread when it truly finishes."""

    def __init__(self, slots: int):
        self.slots = slots
        self._sem = threading.BoundedSemaphore(slots)
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        if self._sem.acquire(blocking=False):
            with self._lock:
                self._active += 1
            return True
        return False

    def release(self) -> None:
        with self._lock:
            self._active -= 1
        self._sem.release()

    @property
    def active(self) -> int:
        return self._active


class RateLimiter:
    """In-process sliding-window limiter keyed by client identity.

    Per worker process only; production deployments should also rate-limit at
    the gateway/load balancer (see docs/deployment.md).
    """

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self._hits) > 10000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v][:5000]:
                    del self._hits[k]
            return True


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than ``max_bytes`` on the given path prefixes (HTTP 413).

    Checks Content-Length up front and counts streamed bytes (covers chunked uploads).
    """

    def __init__(self, app: Callable, max_bytes: int, path_prefixes: tuple[str, ...]):
        self.app = app
        self.max_bytes = max_bytes
        self.path_prefixes = path_prefixes

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"error": {"code": "UPLOAD_TOO_LARGE", "message": "Request body exceeds the maximum upload size.",
                                     "requestId": None, "details": {"maxBytes": self.max_bytes}}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.path_prefixes):
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await self._reject(send)
                        return
                except ValueError:
                    pass
        received = 0
        started = False

        async def counting_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(send)
