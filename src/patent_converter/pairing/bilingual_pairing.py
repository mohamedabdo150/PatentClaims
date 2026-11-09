"""Number-keyed bilingual pairing and streaming JSON response assembly.

Claims are paired exclusively by normalized claim number (never by position).
The response is written to disk as exactly ``{"claims":[{"claimNumber","claimAr","claimEn"}]}``;
HTML is JSON-escaped in chunks so no claim's full HTML has to be in memory.
"""

from __future__ import annotations

import json
from json.encoder import encode_basestring
from pathlib import Path

from ..api.errors import AppError, ErrorCode
from ..extraction.claim_number_parser import claim_sort_key
from ..pipeline.converter import ConvertedDocument

CHUNK_CHARS = 512 * 1024


def pair_claims(en: ConvertedDocument, ar: ConvertedDocument) -> list[tuple[str, Path, Path]]:
    en_keys, ar_keys = set(en.claims), set(ar.claims)
    if en_keys != ar_keys:
        raise AppError(ErrorCode.CLAIM_PAIRING_MISMATCH, "Arabic and English claim numbers do not match.", {
            "missingArabicClaims": sorted(en_keys - ar_keys, key=claim_sort_key),
            "missingEnglishClaims": sorted(ar_keys - en_keys, key=claim_sort_key),
            "duplicateArabicClaims": [], "duplicateEnglishClaims": [],
        })
    return [(n, ar.claims[n], en.claims[n]) for n in sorted(en_keys, key=claim_sort_key)]


class _LimitedWriter:
    def __init__(self, fh, limit: int):
        self.fh = fh
        self.limit = limit
        self.size = 0

    def write(self, s: str) -> None:
        data = s.encode("utf-8")
        self.size += len(data)
        if self.size > self.limit:
            raise AppError(ErrorCode.OUTPUT_LIMIT_EXCEEDED,
                           "The complete response exceeds the configured maximum response size.",
                           {"maxBytes": self.limit, "exceededAfterBytes": self.size})
        self.fh.write(data)


def _write_html_string(w: _LimitedWriter, path: Path) -> None:
    w.write('"')
    with path.open("r", encoding="utf-8") as src:
        while True:
            chunk = src.read(CHUNK_CHARS)
            if not chunk:
                break
            w.write(encode_basestring(chunk)[1:-1])
    w.write('"')


def assemble_response(pairs: list[tuple[str, Path, Path]], out: Path, max_bytes: int) -> int:
    with out.open("wb") as fh:
        w = _LimitedWriter(fh, max_bytes)
        w.write('{"claims":[')
        for i, (number, ar_path, en_path) in enumerate(pairs):
            if i:
                w.write(",")
            w.write('{"claimNumber":' + json.dumps(number) + ',"claimAr":')
            _write_html_string(w, ar_path)
            w.write(',"claimEn":')
            _write_html_string(w, en_path)
            w.write("}")
        w.write("]}")
        return w.size
