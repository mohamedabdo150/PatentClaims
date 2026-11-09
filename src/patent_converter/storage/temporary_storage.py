"""Per-request isolated temporary workspaces with guaranteed cleanup."""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from pathlib import Path

log = logging.getLogger("patent_converter.storage")
JOBS_DIR = "jobs"
RESPONSES_DIR = "responses"


class JobWorkspace:
    """``<root>/jobs/<request-id>-<random>/`` created 0700; removed on exit when cleanup is enabled."""

    def __init__(self, root: Path, request_id: str, cleanup: bool = True):
        self.root = Path(root)
        self.cleanup = cleanup
        safe = "".join(ch for ch in request_id if ch.isalnum() or ch in "-_")[:64] or "job"
        self.path = self.root / JOBS_DIR / f"{safe}-{uuid.uuid4().hex[:12]}"

    def __enter__(self) -> "JobWorkspace":
        (self.root / JOBS_DIR).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.mkdir(mode=0o700)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.cleanup:
            self.remove()

    def file(self, name: str) -> Path:
        candidate = (self.path / name).resolve()
        if candidate.parent != self.path.resolve():
            raise ValueError("invalid workspace file name")
        return candidate

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def sweep_stale_jobs(root: Path, max_age_minutes: int) -> int:
    """Remove job directories and undelivered responses older than the retention window."""
    cutoff = time.time() - max_age_minutes * 60
    removed = 0
    for sub in (JOBS_DIR, RESPONSES_DIR):
        base = Path(root) / sub
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                    removed += 1
            except FileNotFoundError:
                continue
    if removed:
        log.info("removed stale job directories", extra={"count": removed})
    return removed


def free_disk_bytes(root: Path) -> int:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free


def directory_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except FileNotFoundError:
                pass
    return total
