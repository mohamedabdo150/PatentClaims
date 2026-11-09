"""Per-document conversion: extract -> verify -> prepare assets -> render -> sanitize -> write.

Every stage either fully succeeds or raises a structured ``AppError``; a claim
is never written with missing content. Each claim's final HTML lives in its own
file in the job workspace; only one claim's HTML skeleton is in memory at a time.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..api.errors import AppError, ErrorCode
from ..config.claim_patterns import CompiledProfile
from ..config.settings import Settings
from ..extraction.claim_extractor import extract_claims, extract_document
from ..extraction.model_builder import ContentPolicy
from ..html.sanitizer import sanitize
from ..images.asset_pipeline import AssetPipeline
from ..ooxml.package_reader import DocxPackage
from ..renderers.html_renderer import HtmlRenderer
from ..renderers.html_writer import write_html
from ..security.resource_limits import Deadline
from ..validation.integrity import check_document, check_extraction, check_rendering

# Warnings that become errors when the request asks for strictValidation.
STRICT_ESCALATE = {"UNMAPPED_SYMBOL_CHAR", "DINGBAT_FONT_TEXT", "NOTE_CONTENT_NOT_INCLUDED", "HIDDEN_TEXT_RENDERED",
                   "HYPERLINK_SCHEME_DROPPED", "UNKNOWN_ELEMENT_IGNORED", "EQUATION_RENDERED_AS_IMAGE"}


@dataclass
class ConvertedDocument:
    language: str
    claims: dict[str, Path]                 # claim number -> HTML file
    order: list[str]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def policy_from(settings: Settings) -> ContentPolicy:
    c = settings.content
    return ContentPolicy(tracked_changes=c.tracked_changes, hidden_text=c.hidden_text, equation_mode=c.equation_mode,
                         preserve_font_family=c.preserve_font_family, preserve_font_size=c.preserve_font_size)


def convert_document(
    path: Path, language: str, profile: CompiledProfile, settings: Settings, analysis: dict[str, Any],
    workdir: Path, deadline: Deadline, strict: bool = False,
) -> ConvertedDocument:
    timings: dict[str, float] = {}
    t = time.perf_counter()
    doc = extract_claims(path, language, profile, settings, policy_from(settings), deadline)
    timings["extract"] = time.perf_counter() - t
    lang_key = "english" if language == "en" else "arabic"

    problems = check_extraction(analysis, doc.claims)
    if problems:
        raise AppError(ErrorCode.CONTENT_INTEGRITY_FAILED,
                       "Extracted content does not match the analyzed document.", {lang_key: problems[:50]})
    return _render(doc, path, settings, analysis, workdir, deadline, strict, timings, lang_key, "pc-claim")


def convert_whole_document(
    path: Path, language: str, settings: Settings, analysis: dict[str, Any], workdir: Path, deadline: Deadline,
    strict: bool = False,
) -> ConvertedDocument:
    """Entire document body as one HTML fragment (``claims`` holds one entry, key "document")."""
    timings: dict[str, float] = {}
    t = time.perf_counter()
    doc = extract_document(path, language, settings, policy_from(settings), deadline)
    timings["extract"] = time.perf_counter() - t
    problems = check_document(analysis, doc.claims[0])
    if problems:
        raise AppError(ErrorCode.CONTENT_INTEGRITY_FAILED,
                       "Extracted content does not match the analyzed document.", {"document": problems})
    return _render(doc, path, settings, analysis, workdir, deadline, strict, timings, "document", "pc-document")


def _render(doc, path: Path, settings: Settings, analysis: dict[str, Any], workdir: Path, deadline: Deadline,
            strict: bool, timings: dict[str, float], lang_key: str, wrapper_class: str) -> ConvertedDocument:
    language = doc.language

    errors = []
    warnings: Counter = Counter()
    for claim in doc.claims:
        for issue in claim.issues:
            if issue.severity == "error" or (strict and issue.code in STRICT_ESCALATE):
                errors.append({"claimNumber": claim.number, "code": issue.code, "message": issue.message,
                               "count": issue.count})
            else:
                warnings[issue.code] += issue.count
    if errors:
        raise AppError(ErrorCode.UNSUPPORTED_CONTENT,
                       "Claims contain content that cannot be converted faithfully.", {lang_key: errors[:100]})

    t = time.perf_counter()
    with DocxPackage(path) as pkg:
        asset_stats = AssetPipeline(settings, workdir / "assets", deadline.check).prepare(doc.registry, pkg)
    timings["assets"] = time.perf_counter() - t

    t = time.perf_counter()
    renderer = HtmlRenderer(doc.registry, wrapper_class)
    out_dir = workdir / "claims"
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    asset_failures, integrity = [], []
    totals: Counter = Counter()
    html_bytes = 0
    for idx, claim in enumerate(doc.claims):
        deadline.check()
        skeleton, counts = renderer.render_claim(claim)
        if counts.missing_assets:
            for aid in counts.missing_assets:
                a = doc.registry.assets.get(aid)
                asset_failures.append({"claimNumber": claim.number, "asset": a.purpose if a else "unknown",
                                       "code": (a.error if a else None) or "ASSET_MISSING"})
            continue
        clean, report = sanitize(skeleton)
        if not report.clean or clean != skeleton:
            raise AppError(ErrorCode.CONTENT_INTEGRITY_FAILED, "HTML sanitizer would modify generated content.",
                           {lang_key: {"claimNumber": claim.number,
                                       "removed": (report.removed_elements + report.removed_attributes)[:20]}})
        integrity.extend(check_rendering(claim, counts.as_dict()))
        path_out = out_dir / f"{idx:05d}.html"
        html_bytes += write_html(clean, doc.registry, path_out)
        files[claim.number] = path_out
        k = claim.counters
        for name in ("images", "equations", "equations_mathml", "equations_image", "tables", "tables_rasterized",
                     "textboxes", "drawings_rasterized", "floating_objects", "symbol_chars_mapped"):
            totals[name] += getattr(k, name)
        totals["images_embedded"] += counts.images
        claim.blocks = []  # release the model as soon as the claim is written
    timings["render"] = time.perf_counter() - t
    if asset_failures:
        raise AppError(ErrorCode.ASSET_CONVERSION_FAILED, "One or more images could not be converted.",
                       {lang_key: asset_failures[:100]})
    if integrity:
        raise AppError(ErrorCode.CONTENT_INTEGRITY_FAILED, "Rendered HTML does not contain all extracted content.",
                       {lang_key: integrity[:50]})

    a = analysis
    per_claim = a["claims"]["perClaim"] if lang_key != "document" else []
    diagnostics = {
        "claimsDetected": a["claims"]["count"] if lang_key != "document" else 0,
        "claimsExtracted": len(doc.claims),
        "claimsRendered": len(files),
        "imageReferencesDetected": (sum(c["imageReferences"] for c in per_claim) if per_claim or lang_key != "document"
                                    else a["media"]["imageReferencesInMainDocument"]),
        "imagesEmbedded": totals["images_embedded"],
        "equationsDetected": (sum(c["equations"] for c in per_claim) if lang_key != "document"
                              else a["structure"]["counts"].get("equations", 0)),
        "equationsMathml": totals["equations_mathml"],
        "equationsImage": totals["equations_image"],
        "tablesDetected": (sum(c["tables"] for c in per_claim) if lang_key != "document"
                           else a["structure"]["counts"].get("tables", 0)),
        "tablesRendered": totals["tables"],
        "tablesInsideRasterizedDrawings": totals["tables_rasterized"],
        "drawingsRasterized": totals["drawings_rasterized"],
        "textBoxes": totals["textboxes"],
        "floatingObjectsInFlow": totals["floating_objects"],
        "symbolCharsMapped": totals["symbol_chars_mapped"],
        "warnings": dict(warnings),
        "unicodeWarnings": a["unicode"]["claims" if lang_key != "document" else "document"]["warning_count"],
        "htmlValidationFailures": 0,
        "htmlBytes": html_bytes,
        "assets": asset_stats,
        "timingsSeconds": {k: round(v, 3) for k, v in timings.items()},
    }
    return ConvertedDocument(language, files, [c.number for c in doc.claims], diagnostics)
