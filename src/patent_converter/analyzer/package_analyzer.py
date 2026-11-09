"""ZIP/OPC package inspection and security screening.

Works purely from the ZIP central directory (no decompression) so that ZIP
bombs and malformed packages are rejected before any part is parsed.
"""

from __future__ import annotations

import stat
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config.settings import SecuritySettings
from ..ooxml.package_reader import normalize_part_name

_SUPPORTED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


@dataclass
class SecurityFinding:
    code: str
    message: str
    part: str | None = None
    blocking: bool = True


@dataclass
class PackageReport:
    compressed_size: int = 0
    uncompressed_size: int = 0
    entry_count: int = 0
    overall_compression_ratio: float = 0.0
    max_entry_compression_ratio: float = 0.0
    max_ratio_entry: str | None = None
    document_xml_part: str | None = None
    document_xml_size: int = 0
    media_entry_count: int = 0
    media_total_size: int = 0
    embedding_entry_count: int = 0
    embedding_total_size: int = 0
    xml_total_size: int = 0
    has_vba_project: bool = False
    largest_entries: list[dict] = field(default_factory=list)
    findings: list[SecurityFinding] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not any(f.blocking for f in self.findings)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["accepted"] = self.accepted
        return d


def _bad_name(name: str) -> str | None:
    if "\x00" in name:
        return "NUL byte in entry name"
    if "\\" in name:
        return "backslash in entry name"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return "absolute path in entry name"
    if any(part == ".." for part in name.split("/")):
        return "path traversal ('..') in entry name"
    return None


def analyze_package(path: Path | str, limits: SecuritySettings) -> PackageReport:
    path = Path(path)
    report = PackageReport(compressed_size=path.stat().st_size)
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        report.findings.append(SecurityFinding("INVALID_ZIP", f"not a valid ZIP container: {exc}"))
        return report
    with zf:
        infos = zf.infolist()
        report.entry_count = len(infos)
        if report.entry_count > limits.max_zip_entries:
            report.findings.append(SecurityFinding(
                "TOO_MANY_ZIP_ENTRIES", f"{report.entry_count} entries exceeds limit {limits.max_zip_entries}"))
        seen: set[str] = set()
        min_ratio_size = limits.compression_ratio_min_entry_kb * 1024
        for info in infos:
            name = info.filename
            reason = _bad_name(name)
            if reason:
                report.findings.append(SecurityFinding("UNSAFE_ENTRY_NAME", reason, part=name[:200]))
            norm = normalize_part_name(name).lower()
            if norm in seen:
                report.findings.append(SecurityFinding("DUPLICATE_ENTRY", "duplicate ZIP entry name", part=name[:200]))
            seen.add(norm)
            if info.flag_bits & 0x1:
                report.findings.append(SecurityFinding(
                    "ENCRYPTED_ENTRY", "encrypted ZIP entry", part=name[:200], blocking=limits.reject_encrypted_entries))
            if info.compress_type not in _SUPPORTED_COMPRESSION:
                report.findings.append(SecurityFinding(
                    "UNSUPPORTED_COMPRESSION", f"compression method {info.compress_type}", part=name[:200]))
            if stat.S_ISLNK(info.external_attr >> 16):
                report.findings.append(SecurityFinding("SYMLINK_ENTRY", "symbolic link entry", part=name[:200]))
            report.uncompressed_size += info.file_size
            if info.file_size >= min_ratio_size and info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > report.max_entry_compression_ratio:
                    report.max_entry_compression_ratio = round(ratio, 2)
                    report.max_ratio_entry = name[:200]
                if ratio > limits.max_entry_compression_ratio:
                    report.findings.append(SecurityFinding(
                        "SUSPICIOUS_COMPRESSION_RATIO",
                        f"entry compression ratio {ratio:.0f}:1 exceeds {limits.max_entry_compression_ratio:.0f}:1",
                        part=name[:200]))
            lower = norm
            if lower.startswith("word/media/"):
                report.media_entry_count += 1
                report.media_total_size += info.file_size
            elif lower.startswith("word/embeddings/"):
                report.embedding_entry_count += 1
                report.embedding_total_size += info.file_size
            if lower.endswith(".xml") or lower.endswith(".rels"):
                report.xml_total_size += info.file_size
            if lower.endswith("vbaproject.bin"):
                report.has_vba_project = True
        if report.uncompressed_size > limits.max_uncompressed_bytes:
            report.findings.append(SecurityFinding(
                "UNCOMPRESSED_SIZE_EXCEEDED",
                f"total uncompressed size {report.uncompressed_size} exceeds {limits.max_uncompressed_bytes}"))
        if report.compressed_size:
            report.overall_compression_ratio = round(report.uncompressed_size / report.compressed_size, 2)
            if (report.uncompressed_size >= min_ratio_size
                    and report.overall_compression_ratio > limits.max_compression_ratio):
                report.findings.append(SecurityFinding(
                    "SUSPICIOUS_COMPRESSION_RATIO",
                    f"package compression ratio {report.overall_compression_ratio:.0f}:1 exceeds "
                    f"{limits.max_compression_ratio:.0f}:1"))
        if report.has_vba_project:
            report.findings.append(SecurityFinding(
                "MACRO_PROJECT_PRESENT", "VBA project present (macros are never executed)",
                blocking=limits.reject_macro_documents))
        report.largest_entries = [
            {"name": i.filename[:200], "size": i.file_size, "compressedSize": i.compress_size}
            for i in sorted(infos, key=lambda i: i.file_size, reverse=True)[:10]
        ]
    return report
