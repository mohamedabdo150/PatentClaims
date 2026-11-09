#!/usr/bin/env python3
"""Visual review sheet: original DOCX pages (LibreOffice) next to the converted HTML (Chromium).

    python scripts/visual_review.py --en en.docx --ar ar.docx --response response.json --out review/

Produces review/index.html with, per language, the reference rendering of the source document
and the browser rendering of every converted claim side by side, plus PNG screenshots. This is
a human review aid: layout differences are expected (flow layout vs paginated Word layout);
reviewers verify that chemical structures, equations, drawings, tables and text are present
and legible. It does not claim pixel equivalence.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render_docx_pages(docx: Path, out: Path, dpi: int = 96) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    profile = (out / "lo-profile").resolve()
    subprocess.run(["soffice", "--headless", f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf",
                    "--outdir", str(out), str(docx)], check=True, capture_output=True, timeout=600)
    shutil.rmtree(profile, ignore_errors=True)
    pdf = out / (docx.stem + ".pdf")
    subprocess.run(["pdftocairo", "-png", "-r", str(dpi), str(pdf), str(out / docx.stem)], check=True, timeout=600)
    return sorted(out.glob(docx.stem + "-*.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--en", type=Path, required=True)
    ap.add_argument("--ar", type=Path, required=True)
    ap.add_argument("--response", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    claims = json.loads(args.response.read_text(encoding="utf-8"))["claims"]

    rows = "".join(
        f'<tr><th class="n">{html.escape(c["claimNumber"])}</th><td class="en">{c["claimEn"]}</td><td class="ar">{c["claimAr"]}</td></tr>'
        for c in claims)
    converted = args.out / "converted.html"
    converted.write_text(
        '<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,"Noto Naskh Arabic",sans-serif;'
        "font-size:13px}table.review{border-collapse:collapse;width:1260px;table-layout:fixed}th.n{width:40px}td.en,td.ar,th.n{border:1px solid #bbb;"
        "vertical-align:top;padding:6px}</style></head><body><table>" + rows + "</table></body></html>", encoding="utf-8")
    env = {**os.environ}
    subprocess.run(["node", str(ROOT / "tests" / "visual" / "screenshot.js"), str(converted),
                    str(args.out / "converted.png")], check=True, env=env, timeout=600)
    pages = {lang: render_docx_pages(path, args.out / f"source_{lang}") for lang, path in (("en", args.en), ("ar", args.ar))}
    index = ['<!doctype html><html><head><meta charset="utf-8"><title>Visual review</title></head><body>',
             "<h1>Visual review</h1><p>Left: source documents rendered by LibreOffice. Right: converted HTML "
             "rendered by Chromium.</p><table><tr><td style='vertical-align:top'>"]
    for lang, imgs in pages.items():
        index.append(f"<h2>Source ({lang})</h2>")
        index += [f'<img src="{p.relative_to(args.out)}" style="width:600px;border:1px solid #999"><br>' for p in imgs]
    index.append("</td><td style='vertical-align:top'><h2>Converted HTML</h2>"
                 '<img src="converted.png" style="width:900px;border:1px solid #999"></td></tr></table></body></html>')
    (args.out / "index.html").write_text("".join(index), encoding="utf-8")
    print(f"review sheet: {args.out / 'index.html'}  ({sum(len(v) for v in pages.values())} source pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
