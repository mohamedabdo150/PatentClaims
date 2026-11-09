"""Job execution: intake -> security screening -> analysis -> conversion validation.

A job runs entirely in a worker thread inside its own workspace. Cleanup and
job-slot release happen in that thread's ``finally`` so that an HTTP timeout
can never leave files behind or let a second job start while the first is
still consuming resources.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import IO, Any

from ..analyzer.bilingual import analyze_pair
from ..api.errors import AppError, ErrorCode
from ..config.claim_patterns import CompiledProfile
from ..config.settings import Settings
from ..diagnostics import metrics
from ..diagnostics.logging_config import request_id_var
from ..security.resource_limits import Deadline, DeadlineExceeded
from ..security.upload_validator import save_stream, validate_docx_file
from ..storage.temporary_storage import JobWorkspace, free_disk_bytes

log = logging.getLogger("patent_converter.jobs")


@dataclass
class UploadedDocument:
    field: str
    language: str
    stream: IO[bytes]
    filename: str | None
    content_type: str | None
    max_bytes: int


def _record_metrics(result: dict[str, Any]) -> None:
    for key, lang in (("english", "en"), ("arabic", "ar")):
        rep = result.get(key)
        if not rep:
            continue
        metrics.INPUT_BYTES.labels(lang).observe(rep["file"]["sizeBytes"])
        if "claims" in rep:
            metrics.CLAIMS.labels(lang).observe(rep["claims"]["count"])
            metrics.IMAGES.labels(lang).inc(rep["media"]["count"])
            metrics.EQUATIONS.labels(lang).inc(rep["structure"]["counts"].get("equations", 0))
        for risk in rep.get("risks", []):
            if risk["severity"] in ("blocker", "high"):
                metrics.UNSUPPORTED.labels(risk["code"]).inc()
        for phase, secs in rep.get("timingsSeconds", {}).items():
            metrics.PHASE_DURATION.labels(phase).observe(secs)
        res = rep.get("resources", {})
        if res:
            metrics.PEAK_RSS.set(res.get("peak_rss_mb", 0) * 1024 * 1024)
            metrics.PEAK_CPU.set(res.get("peak_cpu_percent", 0))


def validate_for_conversion(result: dict[str, Any]) -> None:
    """Raise the appropriate structured error if the pair cannot be converted completely."""
    rejected = {}
    for key in ("english", "arabic"):
        rep = result[key]
        if rep["status"] == "rejected":
            rejected[key] = [f["code"] for f in rep["package"]["findings"] if f.get("blocking", True)]
    if rejected:
        invalid = all(c in ("INVALID_ZIP", "INVALID_DOCX_STRUCTURE") for codes in rejected.values() for c in codes)
        raise AppError(ErrorCode.INVALID_DOCX if invalid else ErrorCode.DOCUMENT_REJECTED,
                       "One or more documents failed validation or security screening.", {"rejected": rejected})
    extraction = {}
    for key in ("english", "arabic"):
        errors = [i for i in result[key]["claims"]["issues"] if i["severity"] == "error"]
        if errors:
            extraction[key] = [{"code": i["code"], "claimNumber": i["claim_number"], "blockIndex": i["block_index"]}
                               for i in errors]
    if extraction:
        raise AppError(ErrorCode.CLAIM_EXTRACTION_FAILED, "Claim boundaries could not be extracted unambiguously.",
                       {"issues": extraction})
    p = result["pairing"]
    if not p.get("pairable"):
        raise AppError(ErrorCode.CLAIM_PAIRING_MISMATCH, "Arabic and English claim numbers do not match.", {
            "missingArabicClaims": p.get("missingArabicClaims", []),
            "missingEnglishClaims": p.get("missingEnglishClaims", []),
            "duplicateArabicClaims": p.get("duplicateArabicClaims", []),
            "duplicateEnglishClaims": p.get("duplicateEnglishClaims", []),
        })


def run_job(
    settings: Settings,
    profile: CompiledProfile,
    request_id: str,
    uploads: list[UploadedDocument],
    deadline: Deadline,
    mode: str,
    on_finish: Any,
) -> dict[str, Any]:
    """Execute a job synchronously (called in a worker thread)."""
    token = request_id_var.set(request_id)
    t0 = time.perf_counter()
    try:
        st = settings.storage
        if free_disk_bytes(st.temporary_directory) < st.min_free_disk_gb * 1024**3:
            raise AppError(ErrorCode.INSUFFICIENT_STORAGE, "Temporary storage is below the configured free-space minimum.")
        with JobWorkspace(st.temporary_directory, request_id, cleanup=st.cleanup_after_completion) as ws:
            paths = {}
            total = 0
            chunk = settings.processing.upload_chunk_size_kb * 1024
            for up in uploads:
                deadline.check()
                dest = ws.file(f"{up.language}.docx")
                total += save_stream(up.stream, dest, up.max_bytes, chunk, up.field)
                if total > settings.processing.max_combined_bytes:
                    raise AppError(ErrorCode.UPLOAD_TOO_LARGE, "Combined upload exceeds the maximum allowed size.",
                                   {"maxBytes": settings.processing.max_combined_bytes})
                validate_docx_file(dest, up.field, up.filename, up.content_type)
                paths[up.language] = (dest, up.filename)
            metrics.PHASE_DURATION.labels("upload_persist").observe(time.perf_counter() - t0)
            result = analyze_pair(
                settings, profile,
                english=paths["en"][0], arabic=paths["ar"][0],
                english_name=paths["en"][1], arabic_name=paths["ar"][1],
                deadline=deadline,
            )
            _record_metrics(result)
            if mode == "convert":
                validate_for_conversion(result)
                claims = result["pairing"]["englishCount"]
                raise AppError(
                    ErrorCode.CONVERSION_NOT_IMPLEMENTED,
                    "Documents passed validation and claim pairing; HTML rendering is not implemented yet (Phase 4).",
                    {"claimCount": claims, "phase": "2"},
                )
            return result
    except DeadlineExceeded:
        log.warning("job exceeded deadline; aborted")
        raise AppError(ErrorCode.PROCESSING_TIMEOUT, "Processing exceeded the configured time limit.") from None
    finally:
        log.info("job finished", extra={"mode": mode, "seconds": round(time.perf_counter() - t0, 3)})
        request_id_var.reset(token)
        on_finish()
