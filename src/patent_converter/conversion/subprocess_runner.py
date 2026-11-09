"""Run external converters (LibreOffice, poppler) isolated and killable.

Linux / POSIX:
* new session/process group -> the whole tree is killed on timeout,
* resource limits (CPU seconds, max output file size, no core dumps).

Windows:
* the process is started suspended, placed in a Job Object (kill-on-close, optional
  memory limit) and then resumed, so every child (e.g. LibreOffice's soffice.bin) is in
  the job; a timeout terminates the whole job. Without pywin32 the fallback is
  ``taskkill /T /F``.

Both: minimal environment (no proxy variables; private HOME/TEMP inside the job workspace).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

IS_WINDOWS = os.name == "nt"


class ConverterError(Exception):
    pass


class ConverterTimeout(ConverterError):
    pass


def _environment(workdir: Path) -> dict[str, str]:
    home = workdir / "home"
    tmp = workdir / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        keep = ("SystemRoot", "SYSTEMROOT", "WINDIR", "SystemDrive", "PATHEXT", "NUMBER_OF_PROCESSORS",
                "PROCESSOR_ARCHITECTURE", "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env.update({
            "PATH": os.environ.get("PATH", ""),
            "USERPROFILE": str(home), "HOME": str(home),
            "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "TEMP": str(tmp), "TMP": str(tmp),
        })
        return env
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SAL_USE_VCLPLUGIN": "svp",
    }


def run_isolated(cmd: list[str], workdir: Path, timeout: int, max_file_bytes: int = 1 << 30,
                 max_memory_bytes: int = 4 << 30) -> subprocess.CompletedProcess:
    env = _environment(workdir)
    if IS_WINDOWS:
        return _run_windows(cmd, workdir, env, timeout, max_memory_bytes)
    return _run_posix(cmd, workdir, env, timeout, max_file_bytes)


# --------------------------------------------------------------------------- POSIX
def _run_posix(cmd, workdir, env, timeout, max_file_bytes) -> subprocess.CompletedProcess:
    import resource
    import signal

    def limits() -> None:
        cpu = timeout + 10
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    proc = subprocess.Popen(
        cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, preexec_fn=limits,
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


# --------------------------------------------------------------------------- Windows
CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _create_job(max_memory_bytes: int):
    try:
        import win32job
    except ImportError:
        return None
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    flags = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | win32job.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    if max_memory_bytes:
        flags |= win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
        info["JobMemoryLimit"] = max_memory_bytes
    info["BasicLimitInformation"]["LimitFlags"] = flags
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def _run_windows(cmd, workdir, env, timeout, max_memory_bytes) -> subprocess.CompletedProcess:
    import ctypes

    job = _create_job(max_memory_bytes)
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | (CREATE_SUSPENDED if job is not None else 0)
    proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=flags)
    try:
        if job is not None:
            import win32job
            win32job.AssignProcessToJobObject(job, int(proc._handle))  # noqa: SLF001
            ctypes.windll.ntdll.NtResumeProcess(ctypes.c_void_p(int(proc._handle)))  # noqa: SLF001
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if job is not None:
                import win32job
                win32job.TerminateJobObject(job, 1)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
            proc.communicate()
            raise ConverterTimeout(f"{Path(cmd[0]).name} exceeded {timeout}s") from None
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        if job is not None:
            _drain_job(job)


def _drain_job(job, wait_seconds: float = 10.0) -> None:
    """Terminate every process still in the job (e.g. a LibreOffice child that outlived the
    launcher) and wait until none is left: a live process would keep the job workspace locked."""
    import time

    import win32api
    import win32job

    try:
        win32job.TerminateJobObject(job, 0)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
            if info["ActiveProcesses"] == 0:
                break
            time.sleep(0.05)
    finally:
        win32api.CloseHandle(job)
