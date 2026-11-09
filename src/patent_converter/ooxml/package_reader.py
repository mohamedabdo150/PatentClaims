"""Size-limited read access to a DOCX (OPC/ZIP) package.

Nothing is extracted blindly: parts are opened as streams on demand, and every
read is bounded by the size declared in the central directory *and* by a
caller-supplied limit.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from typing import IO
from urllib.parse import unquote

from .namespaces import CT, RT_OFFICE_DOCUMENT, RT_OFFICE_DOCUMENT_STRICT, qn
from .relationships import Relationship, parse_relationships, rels_part_for
from .xml_parser import parse_bytes


class PackageError(Exception):
    """Structural problem with the package (not a valid DOCX)."""


class PartTooLargeError(PackageError):
    pass


class _LimitedReader:
    """Stream wrapper that aborts when more than ``limit`` bytes are produced."""

    def __init__(self, stream: IO[bytes], limit: int, name: str):
        self._stream = stream
        self._limit = limit
        self._name = name
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        self.bytes_read += len(data)
        if self.bytes_read > self._limit:
            raise PartTooLargeError(f"part {self._name} exceeds limit of {self._limit} bytes")
        return data

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "_LimitedReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def normalize_part_name(name: str) -> str:
    return unquote(name).lstrip("/")


class DocxPackage:
    """Read-only view of a DOCX package. Use as a context manager."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        try:
            self._zip = zipfile.ZipFile(self.path, "r")
        except zipfile.BadZipFile as exc:
            raise PackageError(f"not a valid ZIP container: {exc}") from exc
        self._infos: dict[str, zipfile.ZipInfo] = {}
        for info in self._zip.infolist():
            # First occurrence wins; duplicates are reported by the package analyzer.
            self._infos.setdefault(normalize_part_name(info.filename), info)
        self._content_types: tuple[dict[str, str], dict[str, str]] | None = None
        self._rels_cache: dict[str, dict[str, Relationship]] = {}

    # -- context management -------------------------------------------------
    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "DocxPackage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- entries ------------------------------------------------------------
    @property
    def infos(self) -> list[zipfile.ZipInfo]:
        return self._zip.infolist()

    def has_part(self, name: str) -> bool:
        return normalize_part_name(name) in self._infos

    def part_size(self, name: str) -> int:
        return self._infos[normalize_part_name(name)].file_size

    def part_names(self) -> list[str]:
        return list(self._infos)

    def open_part(self, name: str, limit: int) -> _LimitedReader:
        key = normalize_part_name(name)
        info = self._infos.get(key)
        if info is None:
            raise PackageError(f"missing part {key}")
        if info.file_size > limit:
            raise PartTooLargeError(f"part {key} is {info.file_size} bytes; limit {limit}")
        return _LimitedReader(self._zip.open(info, "r"), limit, key)

    def open_part_seekable(self, name: str, limit: int) -> IO[bytes]:
        """Seekable stream (needed by Pillow). zipfile enforces the declared size and CRC."""
        key = normalize_part_name(name)
        info = self._infos.get(key)
        if info is None:
            raise PackageError(f"missing part {key}")
        if info.file_size > limit:
            raise PartTooLargeError(f"part {key} is {info.file_size} bytes; limit {limit}")
        return self._zip.open(info, "r")

    def read_part(self, name: str, limit: int) -> bytes:
        with self.open_part(name, limit) as fh:
            return fh.read()

    # -- OPC metadata -------------------------------------------------------
    def content_types(self, limit: int) -> tuple[dict[str, str], dict[str, str]]:
        """Return (defaults by extension, overrides by part name)."""
        if self._content_types is None:
            if not self.has_part("[Content_Types].xml"):
                raise PackageError("missing [Content_Types].xml")
            root = parse_bytes(self.read_part("[Content_Types].xml", limit))
            defaults = {
                el.get("Extension", "").lower(): el.get("ContentType", "")
                for el in root.iter(qn(CT, "Default"))
            }
            overrides = {
                normalize_part_name(el.get("PartName", "")): el.get("ContentType", "")
                for el in root.iter(qn(CT, "Override"))
            }
            self._content_types = (defaults, overrides)
        return self._content_types

    def content_type_of(self, name: str, limit: int) -> str:
        defaults, overrides = self.content_types(limit)
        key = normalize_part_name(name)
        if key in overrides:
            return overrides[key]
        ext = posixpath.splitext(key)[1].lstrip(".").lower()
        return defaults.get(ext, "")

    def relationships(self, source_part: str, limit: int) -> dict[str, Relationship]:
        """Relationships of ``source_part`` ('' for the package root)."""
        if source_part not in self._rels_cache:
            rels_name = "_rels/.rels" if source_part == "" else rels_part_for(source_part)
            if self.has_part(rels_name):
                self._rels_cache[source_part] = parse_relationships(self.read_part(rels_name, limit), source_part)
            else:
                self._rels_cache[source_part] = {}
        return self._rels_cache[source_part]

    def main_document_part(self, limit: int) -> tuple[str, bool]:
        """Return (main document part name, is_strict_ooxml)."""
        for rel in self.relationships("", limit).values():
            if rel.rel_type in (RT_OFFICE_DOCUMENT, RT_OFFICE_DOCUMENT_STRICT) and rel.part_name:
                if not self.has_part(rel.part_name):
                    raise PackageError(f"main document part {rel.part_name} referenced but missing")
                return rel.part_name, rel.rel_type == RT_OFFICE_DOCUMENT_STRICT
        raise PackageError("no officeDocument relationship in _rels/.rels")


__all__ = ["DocxPackage", "PackageError", "PartTooLargeError", "normalize_part_name"]
