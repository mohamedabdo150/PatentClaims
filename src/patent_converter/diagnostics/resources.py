"""Lightweight process resource sampling (peak RSS / CPU) for a job."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import psutil


@dataclass
class ResourceUsage:
    peak_rss_mb: float = 0.0
    start_rss_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    peak_cpu_percent: float = 0.0
    wall_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 2) for k, v in vars(self).items()}


class ResourceSampler:
    """Samples the current process in a daemon thread. Use as a context manager."""

    def __init__(self, interval: float = 0.2):
        self.interval = interval
        self.usage = ResourceUsage()
        self._proc = psutil.Process()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="resource-sampler")
        self._cpu_samples: list[float] = []
        self._t0 = 0.0

    def _sample(self) -> None:
        rss = self._proc.memory_info().rss / (1024 * 1024)
        self.usage.peak_rss_mb = max(self.usage.peak_rss_mb, rss)
        cpu = self._proc.cpu_percent(None)
        self._cpu_samples.append(cpu)
        self.usage.peak_cpu_percent = max(self.usage.peak_cpu_percent, cpu)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self) -> "ResourceSampler":
        self._t0 = time.perf_counter()
        self.usage.start_rss_mb = self._proc.memory_info().rss / (1024 * 1024)
        self.usage.peak_rss_mb = self.usage.start_rss_mb
        self._proc.cpu_percent(None)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sample()
        self.usage.wall_seconds = time.perf_counter() - self._t0
        if self._cpu_samples:
            self.usage.avg_cpu_percent = sum(self._cpu_samples) / len(self._cpu_samples)
