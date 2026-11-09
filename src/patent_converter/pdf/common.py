"""Shared validation for PDF inputs (raw request bodies of the legacy PDF routes)."""

from __future__ import annotations

from ..api.errors import AppError, ErrorCode
from ..config.settings import MB, PdfSettings


def open_pdf(data: bytes, cfg: PdfSettings):
    """Open a PDF from memory with PyMuPDF after size / signature / page-count checks."""
    import fitz  # PyMuPDF

    if len(data) > cfg.max_pdf_mb * MB:
        raise AppError(ErrorCode.FILE_TOO_LARGE, "PDF exceeds the maximum allowed size.",
                       {"maxBytes": int(cfg.max_pdf_mb * MB)})
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise AppError(ErrorCode.INVALID_PDF, "Request body is not a PDF document.")
    try:
        doc = fitz.open(stream=data, filetype="pdf")  # from memory: no file system or network access
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.INVALID_PDF, f"PDF could not be opened: {type(exc).__name__}") from None
    if doc.needs_pass:
        doc.close()
        raise AppError(ErrorCode.INVALID_PDF, "PDF is password protected.")
    if doc.page_count > cfg.max_pages:
        n = doc.page_count
        doc.close()
        raise AppError(ErrorCode.INVALID_PDF, f"PDF has {n} pages; limit is {cfg.max_pages}.")
    return doc
