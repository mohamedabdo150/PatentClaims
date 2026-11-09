"""Isolated converter processes: the whole process tree is killed on timeout (POSIX and Windows)."""

import sys
import time

import psutil
import pytest

from patent_converter.conversion.subprocess_runner import ConverterTimeout, run_isolated

PARENT = r"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open("child.pid", "w").write(str(child.pid))
time.sleep(120)
"""


def test_timeout_kills_process_tree(tmp_path):
    with pytest.raises(ConverterTimeout):
        run_isolated([sys.executable, "-c", PARENT], tmp_path, timeout=3)
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1")
    res = run_isolated([sys.executable, "-c", "import os; print(sorted(os.environ))"], tmp_path, timeout=30)
    assert res.returncode == 0
    names = res.stdout.decode()
    assert "HTTPS_PROXY" not in names and "https_proxy" not in names
