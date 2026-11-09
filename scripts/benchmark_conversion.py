#!/usr/bin/env python3
"""End-to-end conversion benchmark through a real uvicorn server.

For each EN/AR pair: upload, full conversion, streamed response. Peak RSS is
sampled for the whole server process tree (including LibreOffice children).

    python scripts/benchmark_conversion.py --pairs big/large_10mb big/large_100mb \\
        --max-response-mb 400 --json docs/benchmarks/conversion_synthetic.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def tree_rss(proc: psutil.Process) -> int:
    total = 0
    for p in [proc, *proc.children(recursive=True)]:
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", required=True, help="path prefixes; <prefix>_en.docx and <prefix>_ar.docx")
    ap.add_argument("--profile", default="example")
    ap.add_argument("--max-response-mb", type=float, default=100)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="pcbench-"))
    cfg = work / "app.yaml"
    cfg.write_text(json.dumps({
        "environment": "development",
        "claim_patterns_file": str(ROOT / "config" / "claim_patterns.yaml"),
        "default_config_profile": args.profile,
        "storage": {"temporary_directory": str(work / "data"), "min_free_disk_gb": 1},
        "output": {"max_response_size_mb": args.max_response_mb},
        "security": {"auth_enabled": False, "rate_limit_requests_per_minute": 0},
        "logging": {"level": "WARNING", "json_format": True},
    }))
    env = {**os.environ, "PATENT_CONFIG_FILE": str(cfg), "PYTHONPATH": str(ROOT / "src"), "TMPDIR": str(work)}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "patent_converter.main:create_app", "--factory",
                               "--port", str(args.port), "--log-level", "warning"], env=env)
    proc = psutil.Process(server.pid)
    base = f"http://127.0.0.1:{args.port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    rows = []
    try:
        print(f"{'pair':22} {'in MB':>7} {'status':>6} {'secs':>7} {'out MB':>7} {'peak RSS':>9}  notes")
        for prefix in args.pairs:
            en, ar = Path(prefix + "_en.docx"), Path(prefix + "_ar.docx")
            peak = [tree_rss(proc)]
            stop = threading.Event()

            def sample() -> None:
                while not stop.is_set():
                    peak[0] = max(peak[0], tree_rss(proc))
                    time.sleep(0.1)

            th = threading.Thread(target=sample, daemon=True)
            th.start()
            t = time.perf_counter()
            with en.open("rb") as fe, ar.open("rb") as fa:
                r = httpx.post(base + "/api/v1/patent/claims/convert", timeout=3600,
                               files={"englishFile": (en.name, fe), "arabicFile": (ar.name, fa)})
            secs = time.perf_counter() - t
            stop.set()
            th.join()
            row = {"pair": Path(prefix).name, "inputMB": round((en.stat().st_size + ar.stat().st_size) / 2**20, 1),
                   "status": r.status_code, "seconds": round(secs, 2), "responseMB": round(len(r.content) / 2**20, 1),
                   "peakTreeRssMB": round(peak[0] / 2**20)}
            if r.status_code == 200:
                rid = r.headers["X-Request-ID"]
                d = httpx.get(f"{base}/api/v1/patent/claims/diagnostics/{rid}").json()
                row["claims"] = int(r.headers["X-Claims-Count"])
                row["imagesEmbedded"] = int(r.headers["X-Images-Embedded"])
                row["timings"] = {lang: d[lang]["timingsSeconds"] for lang in ("english", "arabic")}
                note = f"claims={row['claims']} images={row['imagesEmbedded']}"
            else:
                row["error"] = r.json()["error"]["code"]
                note = row["error"]
            rows.append(row)
            print(f"{row['pair']:22} {row['inputMB']:7.1f} {row['status']:>6} {row['seconds']:7.2f} "
                  f"{row['responseMB']:7.1f} {row['peakTreeRssMB']:8}M  {note}")
    finally:
        server.terminate()
        server.wait(10)
    if args.json:
        args.json.write_text(json.dumps({"maxResponseMB": args.max_response_mb, "results": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
