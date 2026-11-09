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


Field = tuple[str, object]   # (key, value); value is a Path (HTML file -> JSON string) or a JSON-serializable value


def assemble_records(records: list[list[Field]], out: Path, max_bytes: int, top_key: str = "claims",
                     extra: list[Field] | None = None) -> int:
    """Stream ``{"<top_key>":[{...}, ...], <extra...>}``; HTML files are JSON-escaped in chunks."""
    with out.open("wb") as fh:
        w = _LimitedWriter(fh, max_bytes)
        w.write("{" + json.dumps(top_key) + ":[")
        for i, record in enumerate(records):
            w.write("," if i else "")
            w.write("{")
            for j, (key, value) in enumerate(record):
                w.write(("," if j else "") + json.dumps(key) + ":")
                if isinstance(value, Path):
                    _write_html_string(w, value)
                else:
                    w.write(json.dumps(value, ensure_ascii=False))
            w.write("}")
        w.write("]")
        for key, value in extra or []:
            w.write("," + json.dumps(key) + ":" + json.dumps(value, ensure_ascii=False))
        w.write("}")
        return w.size


def assemble_object(fields: list[Field], out: Path, max_bytes: int) -> int:
    """Stream a flat JSON object, e.g. ``{"success": true, "html": "..."}``."""
    with out.open("wb") as fh:
        w = _LimitedWriter(fh, max_bytes)
        w.write("{")
        for j, (key, value) in enumerate(fields):
            w.write(("," if j else "") + json.dumps(key) + ":")
            if isinstance(value, Path):
                _write_html_string(w, value)
            else:
                w.write(json.dumps(value, ensure_ascii=False))
        w.write("}")
        return w.size


def assemble_response(pairs: list[tuple[str, Path, Path]], out: Path, max_bytes: int) -> int:
    """New contract: {"claims":[{"claimNumber","claimAr","claimEn"}]}."""
    return assemble_records([[("claimNumber", n), ("claimAr", ar), ("claimEn", en)] for n, ar, en in pairs],
                            out, max_bytes)


def assemble_legacy_claims(docs: dict[str, ConvertedDocument], out: Path, max_bytes: int) -> int:
    """Previous-service contract: {"claims":[{"id":<int>,"claim_english":…,"claim_arabic":…}]}.

    ``id`` is the claim number (claims are paired by number, never by position). A
    language that was not uploaded is omitted from the records, as before.
    """
    numbers = set().union(*(set(d.claims) for d in docs.values()))
    bad = sorted(n for n in numbers if not n.isdigit())
    if bad:
        raise AppError(ErrorCode.NON_NUMERIC_CLAIM_NUMBER,
                       "Claim numbers must be numeric for the legacy id field.", {"claimNumbers": bad[:50]})
    records = []
    for number in sorted(numbers, key=int):
        rec: list[Field] = [("id", int(number))]
        if "en" in docs:
            rec.append(("claim_english", docs["en"].claims[number]))
        if "ar" in docs:
            rec.append(("claim_arabic", docs["ar"].claims[number]))
        records.append(rec)
    return assemble_records(records, out, max_bytes)
