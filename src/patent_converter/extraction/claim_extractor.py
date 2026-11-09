"""Streaming claim extraction into the canonical model.

Second pass over ``document.xml`` (the analyzer pass has already validated the
package, the claim boundaries and the pairing). The same boundary detector is
fed the same blocks, so both passes agree exactly on which blocks belong to
which claim; each claim block is converted to model objects and the XML is
released immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..config.claim_patterns import CompiledProfile
from ..config.settings import Settings
from ..extraction.claim_detector import BlockInfo, ClaimBoundaryDetector, Role
from ..images.assets import AssetRegistry
from ..models.document_model import Claim, ContentIssue
from ..ooxml.namespaces import RT_NUMBERING, RT_STYLES, W, qn
from ..ooxml.numbering import NumberingResolver
from ..ooxml.package_reader import DocxPackage
from ..ooxml.styles import StyleResolver
from ..ooxml.xml_parser import iterparse_secure, parse_bytes
from ..security.resource_limits import Deadline
from ..analyzer.block_scanner import paragraph_text
from .block_stream import iter_blocks, strip_markers
from .model_builder import BuildContext, ContentPolicy, ModelBuilder, count_notes

_W_BODY = qn(W, "body")
_W_P = qn(W, "p")
_W_TBL = qn(W, "tbl")
_W_PPR = qn(W, "pPr")
_W_ALTCHUNK = qn(W, "altChunk")
_W_SECTPR = qn(W, "sectPr")
_NON_CONTENT = {qn(W, n) for n in ("bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd",
                                    "commentRangeStart", "commentRangeEnd", "moveFromRangeStart",
                                    "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd")}


@dataclass
class ExtractedDocument:
    language: str
    claims: list[Claim]
    registry: AssetRegistry
    package_path: Path
    rels: dict = field(default_factory=dict)


def extract_claims(
    path: Path, language: str, profile: CompiledProfile, settings: Settings, policy: ContentPolicy,
    deadline: Deadline | None = None,
) -> ExtractedDocument:
    deadline = deadline or Deadline(None)
    sec = settings.security
    aux = sec.max_auxiliary_xml_bytes
    registry = AssetRegistry()
    claims: list[Claim] = []
    with DocxPackage(path) as pkg:
        main_part, _strict = pkg.main_document_part(aux)
        rels = pkg.relationships(main_part, aux)
        numbering = NumberingResolver()
        styles = StyleResolver()
        for r in rels.values():
            if r.part_name and pkg.has_part(r.part_name):
                if r.rel_type == RT_NUMBERING:
                    numbering.load_numbering(parse_bytes(pkg.read_part(r.part_name, aux)))
                elif r.rel_type == RT_STYLES:
                    root = parse_bytes(pkg.read_part(r.part_name, aux))
                    numbering.load_styles(root)
                    styles.load(root)
        ctx = BuildContext(rels=rels, styles=styles, registry=registry, language=language, policy=policy)
        builder = ModelBuilder(ctx)
        patterns = profile.for_language(language)
        detector = ClaimBoundaryDetector(patterns, profile.validation)
        marker_res = [r for r in (patterns.separator,) if r is not None] if patterns.config.strip_markers else []
        layout_stats: dict = {}
        current: Claim | None = None
        index = 0
        with pkg.open_part(main_part, sec.max_document_xml_bytes) as stream:
            depth = 0
            body_depth: int | None = None
            for event, el in iterparse_secure(stream, huge_tree=sec.xml_huge_tree):
                if event == "start":
                    depth += 1
                    if el.tag == _W_BODY:
                        body_depth = depth
                    continue
                if body_depth is not None and depth == body_depth + 1:
                    deadline.check()
                    for block in iter_blocks(el, patterns, layout_stats):
                        tag = block.tag
                        if not isinstance(tag, str) or tag in _NON_CONTENT or tag == _W_SECTPR:
                            continue
                        kind = {_W_P: "paragraph", _W_TBL: "table", _W_ALTCHUNK: "altchunk"}.get(tag, "other")
                        label = None
                        ctx.labels = {}
                        for p in ([block] if tag == _W_P else block.iter(_W_P)):
                            numpr = numbering.paragraph_numpr(p.find(_W_PPR))
                            if numpr:
                                lab = numbering.next_label(*numpr)
                                ctx.labels[id(p)] = lab.rstrip("\t ") if lab else lab
                                if p is block:
                                    label = lab
                        text = paragraph_text(block) if kind == "paragraph" else ""
                        role = detector.feed(BlockInfo(index=index, kind=kind, text=text, numbering_label=label))
                        if role is Role.CLAIM_START:
                            dc = detector.claims[-1]
                            current = Claim(number=dc.number, number_raw=dc.number_raw, language=language)  # type: ignore[arg-type]
                            claims.append(current)
                        if role in (Role.CLAIM_START, Role.CLAIM_BODY) and current is not None:
                            ctx.claim = current
                            if kind == "paragraph":
                                for rx in marker_res:
                                    block, removed, clean = strip_markers(block, rx)
                                    current.counters.text_chars_removed += removed
                                    if not clean:
                                        ctx.issue("MARKER_OVERLAPS_CONTENT", "error",
                                                  "claim marker overlaps non-text content and cannot be removed")
                            current.blocks.extend(builder.blocks([block]))
                        else:
                            count_notes(block, ctx)
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
        result = detector.finish(settings.processing.max_claims_per_document)
        # In separator mode the number is only known once a claim is closed.
        for model, detected in zip(claims, result.claims):
            model.number, model.number_raw = detected.number, detected.number_raw
        if layout_stats.get("linearized_tables"):
            for model in claims:
                model.issues.append(ContentIssue("LAYOUT_TABLE_LINEARIZED", "warning",
                                                 "claims inside a marker layout table were linearized"))
                break
    return ExtractedDocument(language=language, claims=claims, registry=registry, package_path=path, rels=rels)


def extract_document(
    path: Path, language: str, settings: Settings, policy: ContentPolicy, deadline: Deadline | None = None,
) -> ExtractedDocument:
    """Whole main-document body as one model (no claim detection, markers kept as written)."""
    deadline = deadline or Deadline(None)
    sec = settings.security
    aux = sec.max_auxiliary_xml_bytes
    registry = AssetRegistry()
    model = Claim(number="document", number_raw="", language=language)  # type: ignore[arg-type]
    with DocxPackage(path) as pkg:
        main_part, _strict = pkg.main_document_part(aux)
        rels = pkg.relationships(main_part, aux)
        numbering = NumberingResolver()
        styles = StyleResolver()
        for r in rels.values():
            if r.part_name and pkg.has_part(r.part_name):
                if r.rel_type == RT_NUMBERING:
                    numbering.load_numbering(parse_bytes(pkg.read_part(r.part_name, aux)))
                elif r.rel_type == RT_STYLES:
                    root = parse_bytes(pkg.read_part(r.part_name, aux))
                    numbering.load_styles(root)
                    styles.load(root)
        ctx = BuildContext(rels=rels, styles=styles, registry=registry, language=language, policy=policy)
        ctx.claim = model
        builder = ModelBuilder(ctx)
        with pkg.open_part(main_part, sec.max_document_xml_bytes) as stream:
            depth = 0
            body_depth: int | None = None
            for event, el in iterparse_secure(stream, huge_tree=sec.xml_huge_tree):
                if event == "start":
                    depth += 1
                    if el.tag == _W_BODY:
                        body_depth = depth
                    continue
                if body_depth is not None and depth == body_depth + 1:
                    deadline.check()
                    for block in iter_blocks(el, None):
                        tag = block.tag
                        if not isinstance(tag, str) or tag in _NON_CONTENT or tag == _W_SECTPR:
                            continue
                        ctx.labels = {}
                        for p in ([block] if tag == _W_P else block.iter(_W_P)):
                            numpr = numbering.paragraph_numpr(p.find(_W_PPR))
                            if numpr:
                                lab = numbering.next_label(*numpr)
                                ctx.labels[id(p)] = lab.rstrip("\t ") if lab else lab
                        model.blocks.extend(builder.blocks([block]))
                    el.clear(keep_tail=True)
                    parent = el.getparent()
                    if parent is not None:
                        while el.getprevious() is not None:
                            del parent[0]
                if el.tag == _W_BODY:
                    body_depth = None
                depth -= 1
    return ExtractedDocument(language=language, claims=[model], registry=registry, package_path=path, rels=rels)
