#!/usr/bin/env python3
"""DOCX Capability Analyzer CLI.

Usage:
    python scripts/analyze_docx.py --en english.docx --ar arabic.docx [--profile example]
                                   [--out reports/] [--samples] [--config config/app.dev.yaml]

Writes <name>.analysis.json and <name>.analysis.txt per document plus a
combined bilingual summary. Patent text is NOT included unless --samples.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patent_converter.analyzer.bilingual import analyze_pair, render_pair_summary  # noqa: E402
from patent_converter.config.claim_patterns import PatternRegistry  # noqa: E402
from patent_converter.config.settings import load_settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--en", type=Path, help="English DOCX")
    ap.add_argument("--ar", type=Path, help="Arabic DOCX")
    ap.add_argument("--profile", default=None, help="claim pattern profile name")
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "app.dev.yaml")
    ap.add_argument("--out", type=Path, default=None, help="directory for JSON/text reports")
    ap.add_argument("--samples", action="store_true", help="include short claim-start text samples (confidential!)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not args.en and not args.ar:
        ap.error("provide --en and/or --ar")

    overrides = {"analysis": {"include_text_samples": True}} if args.samples else {}
    settings = load_settings(args.config, **overrides)
    registry = PatternRegistry.from_file(settings.claim_patterns_file)
    profile = registry.get(args.profile or settings.default_config_profile)

    result = analyze_pair(settings, profile, english=args.en, arabic=args.ar)
    texts = []
    for key in ("english", "arabic"):
        rep = result.get(key)
        if rep is None:
            continue
        from patent_converter.analyzer.capability_report import render_text
        texts.append(render_text(rep))
    texts.append(render_pair_summary(result))
    output = "\n\n".join(texts)
    if not args.quiet:
        print(output)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for key, src in (("english", args.en), ("arabic", args.ar)):
            if result.get(key) is not None:
                stem = src.stem
                (args.out / f"{stem}.analysis.json").write_text(
                    json.dumps(result[key], ensure_ascii=False, indent=2), encoding="utf-8")
        (args.out / "pair.analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.out / "report.txt").write_text(output, encoding="utf-8")
        if not args.quiet:
            print(f"\nReports written to {args.out}")
    blocking = any(
        r is not None and r["status"] == "rejected" for r in (result.get("english"), result.get("arabic"))
    )
    return 2 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
