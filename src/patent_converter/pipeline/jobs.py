"""Job execution: intake -> security screening -> analysis -> conversion validation.

A job runs entirely in a worker thread inside its own workspace. Cleanup and
job-slot release happen in that thread's ``finally`` so that an HTTP timeout
can never leave files behind or let a second job start while the first is
still consuming resources.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
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
from ..storage.temporary_storage import RESPONSES_DIR, JobWorkspace, free_disk_bytes
from ..config.settings import MB
from ..analyzer.docx_analyzer import DocxAnalyzer
from ..pairing.bilingual_pairing import assemble_legacy_claims, assemble_object, assemble_response, pair_claims
from .converter import convert_document, convert_whole_document

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
    present = [k for k in ("english", "arabic") if result.get(k) is not None]
    rejected = {}
    for key in present:
        rep = result[key]
        if rep["status"] == "rejected":
            rejected[key] = [f["code"] for f in rep["package"]["findings"] if f.get("blocking", True)]
    if rejected:
        invalid = all(c in ("INVALID_ZIP", "INVALID_DOCX_STRUCTURE") for codes in rejected.values() for c in codes)
        raise AppError(ErrorCode.INVALID_DOCX if invalid else ErrorCode.DOCUMENT_REJECTED,
                       "One or more documents failed validation or security screening.", {"rejected": rejected})
    extraction = {}
    for key in present:
        errors = [i for i in result[key]["claims"]["issues"] if i["severity"] == "error"]
        if errors:
            extraction[key] = [{"code": i["code"], "claimNumber": i["claim_number"], "blockIndex": i["block_index"]}
                               for i in errors]
    if extraction:
        raise AppError(ErrorCode.CLAIM_EXTRACTION_FAILED, "Claim boundaries could not be extracted unambiguously.",
                       {"issues": extraction})
    if len(present) < 2:
        return
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
    strict: bool = False,
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
                up_lang_key = "doc" if mode == "document" else up.language
                total += save_stream(up.stream, dest, up.max_bytes, chunk, up.field)
                if total > settings.processing.max_combined_bytes:
                    raise AppError(ErrorCode.UPLOAD_TOO_LARGE, "Combined upload exceeds the maximum allowed size.",
                                   {"maxBytes": settings.processing.max_combined_bytes})
                validate_docx_file(dest, up.field, up.filename, up.content_type)
                paths[up_lang_key] = (dest, up.filename)
            metrics.PHASE_DURATION.labels("upload_persist").observe(time.perf_counter() - t0)
            if mode == "document":
                return _convert_whole(settings, profile, request_id, ws.path, paths, deadline, strict, t0)
            result = analyze_pair(
                settings, profile,
                english=paths["en"][0] if "en" in paths else None,
                arabic=paths["ar"][0] if "ar" in paths else None,
                english_name=paths["en"][1] if "en" in paths else None,
                arabic_name=paths["ar"][1] if "ar" in paths else None,
                deadline=deadline,
            )
            _record_metrics(result)
            if mode in ("convert", "legacy_claims"):
                validate_for_conversion(result)
                return _convert(settings, profile, request_id, ws.path, paths, result, deadline, strict, t0,
                                legacy=mode == "legacy_claims")
            return result
    except DeadlineExceeded:
        log.warning("job exceeded deadline; aborted")
        raise AppError(ErrorCode.PROCESSING_TIMEOUT, "Processing exceeded the configured time limit.") from None
    finally:
        log.info("job finished", extra={"mode": mode, "seconds": round(time.perf_counter() - t0, 3)})
        request_id_var.reset(token)
        on_finish()


def _publish(settings: Settings, request_id: str, response) -> str:
    """Move the response out of the workspace (removed when the job ends); deleted after streaming."""
    out_dir = settings.storage.temporary_directory / RESPONSES_DIR
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = out_dir / f"{request_id}-{uuid.uuid4().hex[:12]}.json"
    os.replace(response, final)
    return str(final)


def _convert_whole(settings, profile, request_id, workdir, paths, deadline, strict, t0) -> dict[str, Any]:
    """Whole-document conversion (legacy /convert_word_to_html); language detected from the text."""
    path, name = paths["doc"]
    analyzer = DocxAnalyzer(settings, profile, deadline, linearize_layout_tables=False)
    analysis = analyzer.analyze(path, "en", display_name=name)
    if analysis["status"] == "rejected":
        codes = [f["code"] for f in analysis["package"]["findings"] if f.get("blocking", True)]
        invalid = all(c in ("INVALID_ZIP", "INVALID_DOCX_STRUCTURE") for c in codes)
        raise AppError(ErrorCode.INVALID_DOCX if invalid else ErrorCode.DOCUMENT_REJECTED,
                       "The document failed validation or security screening.", {"rejected": {"document": codes}})
    uni = analysis["unicode"]["document"]
    language = "ar" if uni["arabic_chars"] > uni["latin_chars"] else "en"
    t = time.perf_counter()
    doc = convert_whole_document(path, language, settings, analysis, workdir / "doc", deadline, strict)
    metrics.PHASE_DURATION.labels("convert_document").observe(time.perf_counter() - t)
    response = workdir / "response.json"
    size = assemble_object([("success", True), ("html", doc.claims["document"])], response,
                           int(settings.output.max_response_size_mb * MB))
    metrics.OUTPUT_BYTES.observe(size)
    diagnostics = {"requestId": request_id, "mode": "document", "language": language, "responseBytes": size,
                   "processingSeconds": round(time.perf_counter() - t0, 3), "document": doc.diagnostics,
                   "analysis": {"risks": analysis["risks"]}}
    return {"responsePath": _publish(settings, request_id, response), "diagnostics": diagnostics}


def _convert(settings, profile, request_id, workdir, paths, analysis, deadline, strict, t0,
             legacy: bool = False) -> dict[str, Any]:
    todo = [(lang, key) for lang, key in (("en", "english"), ("ar", "arabic")) if lang in paths]

    def one(item):
        lang, key = item
        t = time.perf_counter()
        doc = convert_document(paths[lang][0], lang, profile, settings, analysis[key], workdir / lang,
                               deadline, strict)
        metrics.PHASE_DURATION.labels(f"convert_{lang}").observe(time.perf_counter() - t)
        return lang, doc

    parallel = settings.processing.parallel_languages
    if parallel is None:
        parallel = (os.cpu_count() or 1) >= 2 * settings.conversion.libreoffice_parallelism
    if parallel and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(todo), thread_name_prefix="lang") as pool:
            futures = [pool.submit(one, item) for item in todo]
            for f in futures:
                # Stop the other language as soon as one fails.
                f.add_done_callback(lambda f: f.exception() is not None and deadline.cancel())
            errors = [f.exception() for f in futures]  # waits for both
        real = [e for e in errors if e is not None and not isinstance(e, DeadlineExceeded)]
        if real or any(errors):
            # Report the actual failure, not the cancellation it caused in the other language.
            raise (real or [e for e in errors if e is not None])[0]
        docs = dict(f.result() for f in futures)
    else:
        docs = dict(one(item) for item in todo)
    t = time.perf_counter()
    response = workdir / "response.json"
    limit = int(settings.output.max_response_size_mb * MB)
    if legacy:
        if len(docs) == 2:
            pair_claims(docs["en"], docs["ar"])  # raises CLAIM_PAIRING_MISMATCH when the sets differ
        size = assemble_legacy_claims(docs, response, limit)
        count = len(set().union(*(set(d.claims) for d in docs.values())))
    else:
        pairs = pair_claims(docs["en"], docs["ar"])
        size = assemble_response(pairs, response, limit)
        count = len(pairs)
    metrics.PHASE_DURATION.labels("assemble_json").observe(time.perf_counter() - t)
    metrics.OUTPUT_BYTES.observe(size)
    empty = {"imagesEmbedded": 0, "equationsMathml": 0, "equationsImage": 0, "tablesRendered": 0, "warnings": {}}
    diagnostics = {
        "requestId": request_id,
        "profile": profile.name,
        "claimCount": count,
        "responseBytes": size,
        "processingSeconds": round(time.perf_counter() - t0, 3),
        "english": docs["en"].diagnostics if "en" in docs else empty,
        "arabic": docs["ar"].diagnostics if "ar" in docs else empty,
        "analysis": {
            key: {"risks": analysis[key]["risks"], "unicode": analysis[key]["unicode"]["claims"]}
            for key in ("english", "arabic") if analysis.get(key)
        },
    }
    return {"responsePath": _publish(settings, request_id, response), "diagnostics": diagnostics}
