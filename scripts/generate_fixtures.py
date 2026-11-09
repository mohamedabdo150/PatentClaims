#!/usr/bin/env python3
"""Generate synthetic bilingual patent DOCX fixtures (standard set and large benchmark files).

    python scripts/generate_fixtures.py --out fixtures/                 # standard set
    python scripts/generate_fixtures.py --out big/ --large 100 --claims 500

LIMITATION: synthetic documents exercise the OOXML structures but are not a
substitute for representative real patent documents.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fixtures.patent_fixtures import build_edge_cases, build_large, build_patent  # noqa: E402

IMAGE_PX = 1000
BYTES_PER_IMAGE = IMAGE_PX * IMAGE_PX * 3  # incompressible RGB noise PNG ~ raw size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--large", type=float, nargs="*", help="target sizes in MB per language")
    ap.add_argument("--claims", type=int, default=500)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.large:
        for mb in args.large:
            images = max(1, int(mb * 1024 * 1024 / BYTES_PER_IMAGE))
            for lang in ("en", "ar"):
                p = build_large(args.out / f"large_{int(mb)}mb_{lang}.docx", lang, args.claims, images, IMAGE_PX,
                                seed=1 if lang == "en" else 1000, vector_every=25, group_every=50)
                print(f"{p}  {p.stat().st_size / 1024 / 1024:.1f} MB  ({images} images, {args.claims} claims)")
        return 0
    for lang in ("en", "ar"):
        build_patent(args.out / f"patent_{lang}.docx", lang)
        build_patent(args.out / f"patent_{lang}_autonumbered.docx", lang, autonumber=True)
    build_patent(args.out / "patent_ar_missing_claim5.docx", "ar", missing={5})
    build_edge_cases(args.out / "edge_cases_en.docx")
    print(f"fixtures written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
