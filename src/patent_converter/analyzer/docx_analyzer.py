"""DOCX Capability Analyzer.

Inspects one DOCX package and reports what it actually contains so that
conversion support is driven by real document structures, not assumptions.

Memory model: the package is screened from the ZIP central directory first;
``document.xml`` is then streamed with ``iterparse`` and every top-level body
block is scanned and released immediately; images are probed header-only one
at a time. Only small auxiliary parts (rels, numbering, styles, content types)
are parsed fully, each under a configured size limit.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from ..config.claim_patterns import CompiledProfile
from ..config.settings import MB, Settings
from ..extraction.claim_detector import BlockInfo, ClaimBoundaryDetector, Role
from ..ooxml.namespaces import (
    MACRO_CONTENT_TYPES, RT_ENDNOTES, RT_FOOTER, RT_FOOTNOTES, RT_HEADER, RT_HYPERLINK, RT_IMAGE, RT_NUMBERING,
    RT_STYLES, W, qn,
)
from ..ooxml.numbering import NumberingResolver
from ..ooxml.package_reader import DocxPackage, PackageError
from ..ooxml.xml_parser import UnsafeXmlError, iterparse_secure, parse_bytes
from ..unicode_integrity.checks import UnicodeReport, analyze_text
from ..extraction.block_stream import iter_blocks
from .block_scanner import BlockStats, paragraph_text, scan_block
from .capability_report import build_estimates, evaluate_risks
from .image_probe import ImageInfo, configure_pillow_limit, probe_image
from .package_analyzer import SecurityFinding, analyze_package
from ..security.resource_limits import Deadline

_W_BODY = qn(W, "body")
_W_P = qn(W, "p")
_W_TBL = qn(W, "tbl")
_W_SDT = qn(W, "sdt")
_W_SDT_CONTENT = qn(W, "sdtContent")
_W_CUSTOMXML = qn(W, "customXml")
_W_SECTPR = qn(W, "sectPr")
_W_ALTCHUNK = qn(W, "altChunk")
_W_PPR = qn(W, "pPr")
_NON_CONTENT_BODY = {qn(W, n) for n in ("bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd",
                                         "commentRangeStart", "commentRangeEnd", "moveFromRangeStart",
                                         "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")}
_IMAGE_EXTS = {"png", "jpg", "jpeg", "jpe", "gif", "tif", "tiff", "bmp", "emf", "wmf", "svg", "wdp", "hdp", "webp"}


class AnalysisRejected(Exception):
    """Raised (by callers that require acceptance) when a document fails security screening."""

    def __init__(self, report: dict[str, Any]):
        super().__init__("document rejected by security screening")
        self.report = report


@dataclass
class _ClaimAccumulator:
    number: str
    number_raw: str
    start_block: int
    stats: BlockStats = field(default_factory=BlockStats)
    media_refs: list[str] = field(default_factory=list)
    text_chars: int = 0
    text_utf8: int = 0
    unicode: UnicodeReport = field(default_factory=UnicodeReport)
    sample: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class DocxAnalyzer:
    def __init__(self, settings: Settings, profile: CompiledProfile, deadline: Deadline | None = None):
        self.settings = settings
        self.profile = profile
        self.sec = settings.security
        self.deadline = deadline or Deadline(None)
        configure_pillow_limit(self.sec.max_image_pixels)

    # ------------------------------------------------------------------ public
    def analyze(self, path: Path | str, language: str, display_name: str | None = None) -> dict[str, Any]:
        path = Path(path)
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        report: dict[str, Any] = {
            "file": {
                "name": display_name or path.name,
                "language": language,
                "sizeBytes": path.stat().st_size,
                "sha256": _sha256(path),
            },
            "status": "accepted",
        }
        pkg_report = analyze_package(path, self.sec)
        timings["package_scan"] = time.perf_counter() - t0
        report["package"] = pkg_report.to_dict()
        findings: list[SecurityFinding] = list(pkg_report.findings)
        if not pkg_report.accepted:
            return self._finish_rejected(report, findings, timings, t0)
        try:
            with DocxPackage(path) as pkg:
                self._analyze_package_content(pkg, language, report, findings, timings)
        except (PackageError, UnsafeXmlError, etree.XMLSyntaxError) as exc:
            code = "UNSAFE_XML" if isinstance(exc, UnsafeXmlError) else "INVALID_DOCX_STRUCTURE"
            findings.append(SecurityFinding(code, str(exc)[:300]))
            return self._finish_rejected(report, findings, timings, t0)
        report["package"]["findings"] = [vars(f) for f in findings]
        if any(f.blocking for f in findings):
            report["status"] = "rejected"
        timings["total"] = time.perf_counter() - t0
        report["timingsSeconds"] = {k: round(v, 4) for k, v in timings.items()}
        report["risks"] = evaluate_risks(report, self.settings, self.profile)
        return report

    # --------------------------------------------------------------- internals
    def _finish_rejected(self, report: dict, findings: list[SecurityFinding], timings: dict, t0: float) -> dict:
        report["status"] = "rejected"
        report["package"]["findings"] = [vars(f) for f in findings]
        report["package"]["accepted"] = False
        timings["total"] = time.perf_counter() - t0
        report["timingsSeconds"] = {k: round(v, 4) for k, v in timings.items()}
        report["risks"] = evaluate_risks(report, self.settings, self.profile)
        return report

    def _analyze_package_content(
        self, pkg: DocxPackage, language: str, report: dict, findings: list[SecurityFinding], timings: dict
    ) -> None:
        aux = self.sec.max_auxiliary_xml_bytes
        t = time.perf_counter()
        main_part, strict = pkg.main_document_part(aux)
        main_ct = pkg.content_type_of(main_part, aux)
        if strict:
            findings.append(SecurityFinding("STRICT_OOXML_UNSUPPORTED", "ISO/IEC 29500 Strict documents are not supported"))
        if main_ct in MACRO_CONTENT_TYPES:
            findings.append(SecurityFinding(
                "MACRO_ENABLED_DOCUMENT", "macro-enabled main document content type",
                blocking=self.sec.reject_macro_documents))
        rels = pkg.relationships(main_part, aux)
        rel_types = Counter(r.type_name for r in rels.values())
        external = [r for r in rels.values() if r.external]
        unsafe = [r for r in rels.values() if r.unsafe]
        for r in unsafe:
            findings.append(SecurityFinding("UNSAFE_RELATIONSHIP_TARGET", "relationship target escapes package", part=r.rel_id))
        header_footer_images = 0
        for r in rels.values():
            if r.rel_type in (RT_HEADER, RT_FOOTER) and r.part_name and pkg.has_part(r.part_name):
                header_footer_images += sum(
                    1 for hr in pkg.relationships(r.part_name, aux).values() if hr.rel_type == RT_IMAGE)
        report["ooxml"] = {
            "mainDocumentPart": main_part,
            "mainContentType": main_ct,
            "strict": strict,
            "documentXmlBytes": pkg.part_size(main_part),
            "relationshipCount": len(rels),
            "relationshipTypes": dict(rel_types),
            "externalRelationships": dict(Counter(r.type_name for r in external)),
            "externalNonHyperlinkRelationships": sum(1 for r in external if r.rel_type != RT_HYPERLINK),
            "unsafeRelationships": len(unsafe),
            "headers": rel_types.get("header", 0),
            "footers": rel_types.get("footer", 0),
            "headerFooterImages": header_footer_images,
            "hasFootnotes": any(r.rel_type == RT_FOOTNOTES for r in rels.values()),
            "hasEndnotes": any(r.rel_type == RT_ENDNOTES for r in rels.values()),
        }

        resolver = NumberingResolver()
        for r in rels.values():
            if r.part_name and pkg.has_part(r.part_name) and r.rel_type in (RT_NUMBERING, RT_STYLES):
                root = parse_bytes(pkg.read_part(r.part_name, aux))
                (resolver.load_numbering if r.rel_type == RT_NUMBERING else resolver.load_styles)(root)
        timings["metadata"] = time.perf_counter() - t

        t = time.perf_counter()
        self._scan_document(pkg, main_part, rels, language, resolver, report)
        timings["document_scan"] = time.perf_counter() - t

        t = time.perf_counter()
        self._probe_media(pkg, report)
        timings["media_probe"] = time.perf_counter() - t
        report["estimates"] = build_estimates(report, self.settings)

    def _scan_document(self, pkg, main_part, rels, language, resolver, report) -> None:
        patterns = self.profile.for_language(language)
        detector = ClaimBoundaryDetector(patterns, self.profile.validation)
        doc_stats = BlockStats()
        doc_unicode = UnicodeReport()
        block_kinds: Counter = Counter()
        missing_refs: Counter = Counter()
        refs_total = 0
        image_refs_by_part: Counter = Counter()
        auto_numbered = 0
        claims: dict[int, _ClaimAccumulator] = {}
        claim_order: list[int] = []
        layout_stats: dict = {}
        current: _ClaimAccumulator | None = None
        index = 0
        a_cfg = self.settings.analysis

        with pkg.open_part(main_part, self.sec.max_document_xml_bytes) as stream:
            depth = 0
            body_depth: int | None = None
            for event, el in iterparse_secure(stream, huge_tree=self.sec.xml_huge_tree):
                if event == "start":
                    depth += 1
                    if el.tag == _W_BODY:
                        body_depth = depth
                    continue
                if body_depth is not None and depth == body_depth + 1:
                    self.deadline.check()
                    for block_el in iter_blocks(el, patterns, layout_stats):
                        tag = block_el.tag
                        if tag in _NON_CONTENT_BODY or tag == _W_SECTPR or not isinstance(tag, str):
                            if tag == _W_SECTPR:
                                block_kinds["final_section_properties"] += 1
                            continue
                        kind = {_W_P: "paragraph", _W_TBL: "table", _W_ALTCHUNK: "altchunk"}.get(tag, "other")
                        block_kinds[kind] += 1
                        # Advance numbering counters for every paragraph in document order.
                        label = None
                        for p in ([block_el] if tag == _W_P else block_el.iter(_W_P)):
                            numpr = resolver.paragraph_numpr(p.find(_W_PPR))
                            if numpr:
                                lab = resolver.next_label(*numpr)
                                if p is block_el:
                                    label = lab
                        text = paragraph_text(block_el) if kind == "paragraph" else ""
                        role = detector.feed(BlockInfo(index=index, kind=kind, text=text, numbering_label=label))
                        stats = scan_block(block_el)
                        if kind == "other":
                            stats.unknown_elements[f"body-level:{etree.QName(tag).localname}"] += 1
                        doc_stats.merge(stats)
                        block_text = stats.text
                        uni = analyze_text(block_text)
                        doc_unicode.merge(uni)
                        if role is Role.CLAIM_START:
                            dc = detector.claims[-1]
                            current = _ClaimAccumulator(dc.number, dc.number_raw, index)
                            claims[index] = current
                            claim_order.append(index)
                            if dc.number_from_numbering:
                                auto_numbered += 1
                            if a_cfg.include_text_samples:
                                current.sample = text[: a_cfg.text_sample_chars]
                        in_claim = role in (Role.CLAIM_START, Role.CLAIM_BODY) and current is not None
                        for ref in stats.references:
                            refs_total += 1
                            rel = rels.get(ref.rel_id)
                            if rel is None or (not rel.external and (not rel.part_name or not pkg.has_part(rel.part_name))):
                                missing_refs[ref.attribute] += 1
                                continue
                            if rel.rel_type == RT_IMAGE and not rel.external:
                                image_refs_by_part[rel.part_name] += 1
                                if in_claim:
                                    current.media_refs.append(rel.part_name)
                        if in_claim:
                            current.stats.merge(stats)
                            current.text_chars += len(block_text)
                            current.text_utf8 += len(block_text.encode("utf-8", "surrogatepass"))
                            current.unicode.merge(uni)
                            if kind == "paragraph":
                                current.stats.counts["top_level_paragraphs"] += 1
                        if detector.current_claim is None:
                            current = None
                        index += 1
                    el.clear(keep_tail=True)
                    parent = el.getparent()
                    if parent is not None:
                        while el.getprevious() is not None:
                            del parent[0]
                if el.tag == _W_BODY:
                    body_depth = None
                depth -= 1

        result = detector.finish(self.settings.processing.max_claims_per_document)
        claims_unicode = UnicodeReport()
        per_claim = []
        for dc, start in zip(result.claims, claim_order):
            acc = claims[start]
            claims_unicode.merge(acc.unicode)
            cc = acc.stats.counts
            entry = {
                "number": dc.number,
                "numberRaw": dc.number_raw,
                "numberFromNumbering": dc.number_from_numbering,
                "startBlock": dc.start_block,
                "endBlock": dc.end_block,
                "blockCount": dc.block_count,
                "closedBy": dc.closed_by,
                "paragraphs": cc.get("top_level_paragraphs", 0),
                "tables": cc.get("tables", 0),
                "imageReferences": len(acc.media_refs),
                "drawings": cc.get("drawings", 0),
                "vmlPictures": cc.get("vml_pict", 0),
                "equations": cc.get("equations", 0),
                "oleObjects": cc.get("ole_objects", 0),
                "textboxes": cc.get("textboxes", 0),
                "symbols": cc.get("symbols", 0),
                "textChars": acc.text_chars,
                "textUtf8Bytes": acc.text_utf8,
                "mediaParts": acc.media_refs,
                "unicodeWarnings": acc.unicode.warning_count(),
            }
            if acc.sample is not None:
                entry["sample"] = acc.sample
            per_claim.append(entry)

        c = doc_stats.counts
        report["structure"] = {
            "topLevelBlocks": dict(block_kinds),
            "counts": {k: v for k, v in sorted(c.items())},
            "ommlConstructs": dict(doc_stats.omml_constructs),
            "fieldTypes": dict(doc_stats.field_types),
            "symbolFonts": dict(doc_stats.symbol_fonts),
            "oleProgIds": dict(doc_stats.ole_progids),
            "unknownElements": dict(doc_stats.unknown_elements.most_common(100)),
            "relationshipReferences": refs_total,
            "missingRelationshipTargets": dict(missing_refs),
        }
        report["numbering"] = {
            "numberedParagraphs": c.get("numbered_paragraphs", 0),
            "claimsNumberedByWordNumbering": auto_numbered,
            "unsupportedFormats": dict(resolver.unsupported_formats),
        }
        report["claims"] = {
            "profile": self.profile.name,
            "profileVerified": self.profile.verified,
            "endMode": result.end_mode.value,
            "sectionFound": result.section_found,
            "sectionStartBlock": result.section_start_block,
            "sectionEndBlock": result.section_end_block,
            "count": len(result.claims),
            "numbers": result.claim_numbers,
            "duplicates": result.duplicate_numbers,
            "orphanBlocks": len(result.orphan_blocks),
            "layoutTablesLinearized": layout_stats.get("linearized_tables", 0),
            "issues": [vars(i) for i in result.issues],
            "perClaim": per_claim,
        }
        report["unicode"] = {"document": doc_unicode.to_dict(), "claims": claims_unicode.to_dict()}
        report["_imageRefsByPart"] = dict(image_refs_by_part)

    def _probe_media(self, pkg: DocxPackage, report: dict) -> None:
        aux = self.sec.max_auxiliary_xml_bytes
        refs: dict[str, int] = report.pop("_imageRefsByPart", {})
        images: list[ImageInfo] = []
        for name in pkg.part_names():
            lower = name.lower()
            ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
            ct = pkg.content_type_of(name, aux)
            if not (lower.startswith("word/media/") or ct.startswith("image/") or (ext in _IMAGE_EXTS and lower.startswith("word/"))):
                continue
            self.deadline.check()
            size = pkg.part_size(name)
            with pkg.open_part_seekable(name, self.sec.max_uncompressed_bytes) as stream:
                info = probe_image(
                    name, stream, size, self.sec.max_image_pixels, self.sec.max_image_dimension_px,
                    int(self.sec.max_svg_size_mb * MB),
                )
            images.append(info)
        claim_parts = Counter(p for c in report.get("claims", {}).get("perClaim", []) for p in c["mediaParts"])
        formats = Counter(i.detected_format for i in images)
        report["media"] = {
            "count": len(images),
            "totalBytes": sum(i.size_bytes for i in images),
            "formats": dict(formats),
            "referencedByMainDocument": sum(1 for i in images if refs.get(i.part_name)),
            "unreferencedByMainDocument": sum(1 for i in images if not refs.get(i.part_name)),
            "referencedInClaims": len(claim_parts),
            "imageReferencesInClaims": sum(claim_parts.values()),
            "requiringConversion": sum(1 for i in images if i.requires_conversion),
            "maxPixels": max((i.pixels for i in images), default=0),
            "images": [dict(i.to_dict(), referencesInMainDocument=refs.get(i.part_name, 0),
                            referencesInClaims=claim_parts.get(i.part_name, 0)) for i in images],
        }
        report["_imageIndex"] = {i.part_name: i for i in images}
