#!/usr/bin/env python3
"""Benchmark the DOCX Capability Analyzer: wall time and peak RSS per document.

Each document is analyzed in a fresh subprocess so peak RSS is not polluted by
earlier runs.

    python scripts/benchmark_analysis.py big/*.docx --profile example --json results.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1] + "/src")
from patent_converter.analyzer.docx_analyzer import DocxAnalyzer
from patent_converter.config.claim_patterns import PatternRegistry
from patent_converter.config.settings import load_settings
s = load_settings(sys.argv[1] + "/config/app.dev.yaml")
prof = PatternRegistry.from_file(s.claim_patterns_file).get(sys.argv[3])
t = time.perf_counter()
rep = DocxAnalyzer(s, prof).analyze(sys.argv[2], sys.argv[4])
el = time.perf_counter() - t
hwm = int([l.split()[1] for l in open("/proc/self/status") if l.startswith("VmHWM")][0])
print(json.dumps({"seconds": el, "peak_rss_mb": hwm / 1024,
  "claims": rep.get("claims", {}).get("count"), "images": rep.get("media", {}).get("count"),
  "status": rep["status"], "timings": rep["timingsSeconds"],
  "estimated_html_mb": rep.get("estimates", {}).get("estimatedHtmlBytes", 0) / 1048576}))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--profile", default="example")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows = []
    print(f"{'file':34} {'MB':>7} {'claims':>6} {'imgs':>5} {'secs':>7} {'peakRSS':>8} {'estHTML':>8}")
    for f in args.files:
        lang = "ar" if f.stem.endswith("_ar") else "en"
        out = subprocess.run([sys.executable, "-I", "-c", CHILD, str(ROOT), str(f), args.profile, lang],
                             capture_output=True, text=True, check=True)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        r["file"], r["size_mb"] = f.name, f.stat().st_size / 1048576
        rows.append(r)
        print(f"{f.name:34} {r['size_mb']:7.1f} {r['claims']:>6} {r['images']:>5} {r['seconds']:7.2f} "
              f"{r['peak_rss_mb']:7.0f}M {r['estimated_html_mb']:7.1f}M")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
