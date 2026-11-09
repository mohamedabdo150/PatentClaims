"""Streaming upload persistence and file-type validation.

Uploads are copied in bounded chunks from Starlette's spooled temporary file
to the job workspace; the full file is never held in a Python bytes object.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO

from ..api.errors import AppError, ErrorCode

ZIP_LOCAL_HEADER = b"PK\x03\x04"
OLE_CFB = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOCX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
    "application/zip",
    "",
}


def save_stream(src: IO[bytes], dest: Path, max_bytes: int, chunk_size: int, field: str) -> int:
    written = 0
    with dest.open("xb") as out:
        dest.chmod(0o600)
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise AppError(ErrorCode.FILE_TOO_LARGE, f"{field} exceeds the maximum allowed size.",
                               {"field": field, "maxBytes": max_bytes})
            out.write(chunk)
    return written


def validate_docx_file(path: Path, field: str, filename: str | None, content_type: str | None) -> None:
    name = (filename or "").lower()
    if name and not name.endswith(".docx"):
        raise AppError(ErrorCode.UNSUPPORTED_FILE_TYPE, f"{field} must be a .docx file.", {"field": field})
    if (content_type or "").split(";")[0].strip().lower() not in DOCX_CONTENT_TYPES:
        raise AppError(ErrorCode.UNSUPPORTED_FILE_TYPE, f"{field} has an unsupported content type.", {"field": field})
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(OLE_CFB):
        raise AppError(ErrorCode.ENCRYPTED_OR_LEGACY_DOCUMENT,
                       f"{field} is a password-protected or legacy (.doc) Word file.", {"field": field})
    if not head.startswith(ZIP_LOCAL_HEADER):
        raise AppError(ErrorCode.UNSUPPORTED_FILE_TYPE, f"{field} is not a DOCX (ZIP) file.", {"field": field})
