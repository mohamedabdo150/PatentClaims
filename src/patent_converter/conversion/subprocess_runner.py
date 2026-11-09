"""Run external converters (LibreOffice, poppler) isolated and killable.

* new session/process group -> the whole tree is killed on timeout,
* minimal environment (no proxy variables, private HOME/TMPDIR inside the job workspace),
* resource limits (CPU seconds, max output file size, no core dumps).
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
from pathlib import Path


class ConverterError(Exception):
    pass


class ConverterTimeout(ConverterError):
    pass


def _limits(cpu_seconds: int, max_file_bytes: int):
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def run_isolated(cmd: list[str], workdir: Path, timeout: int, max_file_bytes: int = 1 << 30) -> subprocess.CompletedProcess:
    home = workdir / "home"
    tmp = workdir / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SAL_USE_VCLPLUGIN": "svp",
    }
    proc = subprocess.Popen(
        cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, preexec_fn=_limits(timeout + 10, max_file_bytes),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise ConverterTimeout(f"{Path(cmd[0]).name} exceeded {timeout}s") from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
