"""WordprocessingML -> canonical model.

Walks claim blocks in document order and builds ``models.document_model``
objects. Every text node, image reference, equation and table the analyzer
counted is accounted for in ``ClaimCounters`` (as text, as rasterized content or
as removed-by-policy) so that the pipeline can prove nothing was dropped.

Objects that cannot be represented faithfully as HTML (drawing groups, shapes,
canvases, VML drawings, equations outside the MathML converter's coverage) are
registered as *snippets* and rendered to images from their original XML.
Content that cannot be rendered at all (charts, SmartArt, linked-only images,
OLE objects without preview, altChunks) raises a ``ContentIssue`` error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from lxml import etree

from ..analyzer.block_scanner import _choose_alternate, scan_block
from ..equations.omml_to_mathml import omml_to_mathml
from ..images.assets import AssetRegistry
from ..models.document_model import (
    CellBorder, Claim, ContentIssue, DirSpan, Equation, Hyperlink, Image, LineBreak, NoteReference, Paragraph,
    ParagraphFormat, RunFormat, Table, TableCell, TableRow, TextBox, TextRun,
)
from ..ooxml.namespaces import A, ASVG, C, DGM, M, MC, O, PIC, R, RT_IMAGE, V, W, WP, WPC, WPG, WPS, qn
from ..ooxml.relationships import Relationship
from ..ooxml.styles import Border, ParaProps, RunProps, StyleResolver, parse_borders, parse_ppr, parse_rpr
from ..ooxml.symbol_fonts import font_kind, map_symbol_char, map_symbol_text

EMU_PER_PX = 9525
_ARABIC = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
_HIGHLIGHT = {"darkYellow": "#808000", "darkBlue": "#00008b", "darkCyan": "#008b8b", "darkGreen": "#006400",
              "darkMagenta": "#8b008b", "darkRed": "#8b0000", "darkGray": "#a9a9a9", "lightGray": "#d3d3d3"}
_UNDERLINE = {"double": "double", "dotted": "dotted", "dottedHeavy": "dotted", "wave": "wavy", "wavyHeavy": "wavy",
              "wavyDouble": "wavy"}
_SKIP_INLINE = {qn(W, n) for n in ("pPr", "rPr", "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd",
                                    "commentRangeStart", "commentRangeEnd", "lastRenderedPageBreak",
                                    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
                                    "commentReference", "annotationRef", "fldChar", "instrText", "delInstrText",
                                    "sdtPr", "sdtEndPr", "customXmlPr", "smartTagPr", "permStart",
                                    "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart",
                                    "customXmlDelRangeEnd")}
_CONTAINERS = {qn(W, n) for n in ("smartTag", "customXml", "fldSimple", "moveTo")}
_SAFE_SCHEMES = {"http", "https", "mailto"}
_STYLE_DIM = re.compile(r"(width|height)\s*:\s*([0-9.]+)\s*(pt|in|cm|mm|px)?", re.I)
_UNIT_PT = {"pt": 1.0, "in": 72.0, "cm": 28.3465, "mm": 2.83465, "px": 0.75, None: 1.0}


def _twips_to_pt(v: int | None) -> float | None:
    return None if v is None else round(v / 20.0, 2)


def _emu_px(v: int | None) -> float | None:
    return None if v is None else round(v / EMU_PER_PX, 2)


@dataclass
class ContentPolicy:
    tracked_changes: str = "reject"        # reject | accept
    hidden_text: str = "render"            # render | drop
    equation_mode: str = "mathml"          # mathml (image fallback) | image
    preserve_font_family: bool = True
    preserve_font_size: bool = True


@dataclass
class BuildContext:
    rels: dict[str, Relationship]
    styles: StyleResolver
    registry: AssetRegistry
    language: str
    policy: ContentPolicy
    labels: dict[int, str | None] = field(default_factory=dict)  # id(w:p) -> numbering label
    footnotes_seen: int = 0
    endnotes_seen: int = 0
    claim: Claim | None = None
    pending_custom_mark: bool = False

    def issue(self, code: str, severity: str, message: str) -> None:
        assert self.claim is not None
        for existing in self.claim.issues:
            if existing.code == code and existing.message == message:
                existing.count += 1
                return
        self.claim.issues.append(ContentIssue(code, severity, message))


def count_notes(el: etree._Element, ctx: BuildContext) -> None:
    """Advance footnote/endnote numbering for blocks outside claims."""
    for ref in el.iter(qn(W, "footnoteReference"), qn(W, "endnoteReference")):
        if ref.get(qn(W, "customMarkFollows")) in ("1", "true", "on"):
            continue
        if etree.QName(ref).localname == "footnoteReference":
            ctx.footnotes_seen += 1
        else:
            ctx.endnotes_seen += 1


class ModelBuilder:
    def __init__(self, ctx: BuildContext):
        self.ctx = ctx
        self.baseline = ctx.styles.baseline_run()

    # ------------------------------------------------------------------ helpers
    @property
    def counters(self):
        return self.ctx.claim.counters  # type: ignore[union-attr]

    def _image_refs_in(self, el: etree._Element) -> int:
        """Image relationship references inside a subtree (same rule as the analyzer)."""
        n = 0
        stack = [el]
        while stack:
            node = stack.pop()
            if not isinstance(node.tag, str):
                continue
            if node.tag == qn(MC, "AlternateContent"):
                chosen = _choose_alternate(node)
                if chosen is not None:
                    stack.extend(chosen)
                continue
            for k, v in node.attrib.items():
                if (k.startswith("{" + R + "}") and k[len(R) + 2:] in ("embed", "link", "id", "pict")) or \
                        (k == qn(O, "relid") and node.tag.startswith("{" + V)):
                    rel = self.ctx.rels.get(v)
                    if rel is not None and rel.rel_type == RT_IMAGE and not rel.external:
                        n += 1
            stack.extend(node)
        return n

    def _parts_in(self, el: etree._Element) -> dict[str, tuple[str, str]]:
        parts: dict[str, tuple[str, str]] = {}
        for node in el.iter():
            if not isinstance(node.tag, str):
                continue
            for k, v in node.attrib.items():
                if k.startswith("{" + R + "}") or k == qn(O, "relid"):
                    rel = self.ctx.rels.get(v)
                    if rel is not None and not rel.external and rel.part_name:
                        parts[v] = (rel.part_name, rel.rel_type)
        return parts

    def _account_rasterized(self, el: etree._Element) -> None:
        stats = scan_block(el)
        c = self.counters
        c.text_chars_rasterized += len(stats.text)
        c.equations += stats.counts.get("equations", 0)
        c.equations_image += stats.counts.get("equations", 0)
        c.tables_rasterized += stats.counts.get("tables", 0)
        c.image_refs += self._image_refs_in(el)
        c.drawings_rasterized += 1

    # ------------------------------------------------------------------ blocks
    def blocks(self, children) -> list:
        out = []
        for child in children:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == qn(W, "p"):
                out.append(self.paragraph(child))
            elif tag == qn(W, "tbl"):
                out.append(self.table(child))
            elif tag == qn(W, "sdt"):
                content = child.find(qn(W, "sdtContent"))
                if content is not None:
                    out.extend(self.blocks(content))
            elif tag == qn(W, "customXml"):
                out.extend(self.blocks(child))
            elif tag == qn(MC, "AlternateContent"):
                chosen = _choose_alternate(child)
                if chosen is not None:
                    out.extend(self.blocks(chosen))
            elif tag == qn(W, "altChunk"):
                self.ctx.issue("ALTCHUNK", "error", "imported foreign content (altChunk) cannot be rendered")
            elif tag in _SKIP_INLINE or tag in (qn(W, "sectPr"), qn(W, "tblPr"), qn(W, "tcPr")):
                continue
            else:
                self._unknown(child)
        return out

    def _unknown(self, el: etree._Element) -> None:
        text = scan_block(el).text
        if text.strip() or self._image_refs_in(el):
            self.ctx.issue("UNSUPPORTED_ELEMENT", "error",
                           f"element {etree.QName(el).localname} with content cannot be rendered")
        else:
            self.ctx.issue("UNKNOWN_ELEMENT_IGNORED", "warning", f"empty element {etree.QName(el).localname} ignored")
        self.counters.text_chars_removed += len(text)

    # ------------------------------------------------------------------ paragraphs
    def paragraph(self, p: etree._Element) -> Paragraph:
        ppr = p.find(qn(W, "pPr"))
        pstyle_el = ppr.find(qn(W, "pStyle")) if ppr is not None else None
        pstyle = pstyle_el.get(qn(W, "val")) if pstyle_el is not None else None
        props = self.ctx.styles.paragraph_props(pstyle, parse_ppr(ppr))
        fmt = self._para_format(props)
        fmt.numbering_label = self.ctx.labels.get(id(p))
        para = Paragraph(fmt=fmt)
        para.inlines = self.inlines(p, pstyle, RunProps())
        return para

    def _para_format(self, pp: ParaProps) -> ParagraphFormat:
        rtl = bool(pp.bidi)
        f = ParagraphFormat(direction="rtl" if rtl else "ltr")
        jc = pp.jc
        logical = {"left": "start", "start": "start", "right": "end", "end": "end"}.get(jc or "", jc)
        if logical == "end":
            f.alignment = "left" if rtl else "right"
        elif logical in ("center",):
            f.alignment = "center"
        elif logical in ("both", "distribute", "lowKashida", "mediumKashida", "highKashida", "thaiDistribute"):
            f.alignment = "justify"
        f.margin_start_pt = _twips_to_pt(pp.ind_start) if pp.ind_start else None
        f.margin_end_pt = _twips_to_pt(pp.ind_end) if pp.ind_end else None
        if pp.ind_hanging:
            f.text_indent_pt = -_twips_to_pt(pp.ind_hanging)  # type: ignore[operator]
        elif pp.ind_first:
            f.text_indent_pt = _twips_to_pt(pp.ind_first)
        f.space_before_pt = _twips_to_pt(pp.space_before) if pp.space_before else None
        f.space_after_pt = _twips_to_pt(pp.space_after) if pp.space_after is not None else None
        if pp.line and (pp.line_rule in (None, "auto")) and pp.line != 240:
            f.line_height = round(pp.line / 240.0, 3)
        return f

    # ------------------------------------------------------------------ inline content
    def inlines(self, parent: etree._Element, pstyle: str | None, inherited: RunProps) -> list:
        out: list = []
        for child in parent:
            self._inline(child, pstyle, out)
        return _merge_runs(out)

    def _inline(self, el: etree._Element, pstyle: str | None, out: list) -> None:
        tag = el.tag
        if not isinstance(tag, str) or tag in _SKIP_INLINE:
            return
        if tag == qn(W, "r"):
            self.run(el, pstyle, out)
        elif tag == qn(W, "hyperlink"):
            href = None
            rid = el.get(qn(R, "id"))
            if rid and rid in self.ctx.rels and self.ctx.rels[rid].external:
                target = self.ctx.rels[rid].target
                if urlparse(target).scheme.lower() in _SAFE_SCHEMES:
                    href = target
                else:
                    self.ctx.issue("HYPERLINK_SCHEME_DROPPED", "warning", "hyperlink with unsupported scheme kept as text")
            inner: list = []
            for c in el:
                self._inline(c, pstyle, inner)
            out.append(Hyperlink(_merge_runs(inner), href))
        elif tag in _CONTAINERS:
            for c in el:
                self._inline(c, pstyle, out)
        elif tag == qn(W, "sdt"):
            content = el.find(qn(W, "sdtContent"))
            if content is not None:
                for c in content:
                    self._inline(c, pstyle, out)
        elif tag in (qn(W, "dir"), qn(W, "bdo")):
            inner = []
            for c in el:
                self._inline(c, pstyle, inner)
            out.append(DirSpan(_merge_runs(inner), "rtl" if el.get(qn(W, "val")) == "rtl" else "ltr",
                               override=tag == qn(W, "bdo")))
        elif tag == qn(W, "ins"):
            self._tracked(el)
            for c in el:
                self._inline(c, pstyle, out)
        elif tag in (qn(W, "del"), qn(W, "moveFrom")):
            self._tracked(el)
            self.counters.text_chars_removed += len(scan_block(el).text)
            self.counters.image_refs += self._image_refs_in(el)
        elif tag == qn(M, "oMathPara"):
            for om in el.iter(qn(M, "oMath")):
                out.append(self.equation(om, display=True))
        elif tag == qn(M, "oMath"):
            out.append(self.equation(el, display=False))
        elif tag == qn(MC, "AlternateContent"):
            chosen = _choose_alternate(el)
            if chosen is not None:
                for c in chosen:
                    self._inline(c, pstyle, out)
        else:
            self._unknown(el)

    def _tracked(self, el: etree._Element) -> None:
        if self.ctx.policy.tracked_changes == "reject":
            self.ctx.issue("TRACKED_CHANGES", "error",
                           "claim contains unaccepted tracked changes (policy: reject); accept or reject them in Word")

    # ------------------------------------------------------------------ runs
    def _run_format(self, props: RunProps, complex_script: bool) -> tuple[RunFormat, str | None]:
        bold = props.bold_cs if complex_script and props.bold_cs is not None else props.bold
        italic = props.italic_cs if complex_script and props.italic_cs is not None else props.italic
        size = props.size_cs if complex_script and props.size_cs is not None else props.size
        font = (props.font_cs if complex_script else props.font_ascii) or props.font_ascii
        base_font = (self.baseline.font_cs if complex_script else self.baseline.font_ascii) or self.baseline.font_ascii
        base_size = self.baseline.size_cs if complex_script and self.baseline.size_cs else self.baseline.size
        underline = None
        if props.underline and props.underline != "none":
            underline = _UNDERLINE.get(props.underline, "dashed" if "dash" in props.underline.lower() else "solid")
        highlight = None
        if props.highlight:
            highlight = _HIGHLIGHT.get(props.highlight, props.highlight.lower())
        elif props.shading and re.fullmatch(r"[0-9A-Fa-f]{6}", props.shading):
            highlight = "#" + props.shading.upper()
        color = "#" + props.color.upper() if props.color and re.fullmatch(r"[0-9A-Fa-f]{6}", props.color) else None
        va = {"superscript": "superscript", "subscript": "subscript"}.get(props.vert_align or "", "baseline")
        p = self.ctx.policy
        fmt = RunFormat(
            bold=bool(bold), italic=bool(italic), underline=underline, strike=bool(props.strike or props.dstrike),
            vertical_align=va,  # type: ignore[arg-type]
            font_family=font if p.preserve_font_family and font and font != base_font else None,
            font_size_pt=(size / 2.0) if p.preserve_font_size and size and size != base_size else None,
            color=color if color not in ("#000000",) else None, highlight=highlight,
            caps=bool(props.caps), small_caps=bool(props.small_caps),
        )
        return fmt, font

    def run(self, r: etree._Element, pstyle: str | None, out: list) -> None:
        rpr = r.find(qn(W, "rPr"))
        rstyle_el = rpr.find(qn(W, "rStyle")) if rpr is not None else None
        rstyle = rstyle_el.get(qn(W, "val")) if rstyle_el is not None else None
        props = self.ctx.styles.run_props(pstyle, rstyle, parse_rpr(rpr))
        text_sample = "".join(t.text or "" for t in r.iter(qn(W, "t")))
        complex_script = bool(props.rtl or props.cs or _ARABIC.search(text_sample))
        fmt, font = self._run_format(props, complex_script)
        kind = font_kind(font)
        hidden = bool(props.vanish)
        c = self.counters
        for child in r:
            tag = child.tag
            if not isinstance(tag, str) or tag in _SKIP_INLINE:
                continue
            if tag == qn(W, "t"):
                text = child.text or ""
                if hidden and self.ctx.policy.hidden_text == "drop":
                    c.text_chars_removed += len(text)
                    continue
                if hidden:
                    self.ctx.issue("HIDDEN_TEXT_RENDERED", "warning", "hidden (vanish) text is rendered visibly")
                c.text_chars += len(text)
                if kind == "symbol":
                    mapped, unmapped = map_symbol_text(text)
                    c.symbol_chars_mapped += len(text) - unmapped
                    if unmapped:
                        self.ctx.issue("UNMAPPED_SYMBOL_CHAR", "warning", "Symbol-font characters without Unicode mapping")
                    out.append(TextRun(mapped, _without_font(fmt)))
                else:
                    if kind == "dingbat":
                        self.ctx.issue("DINGBAT_FONT_TEXT", "warning", f"text in dingbat font {font} kept with its font")
                    out.append(TextRun(text, fmt))
            elif tag in (qn(W, "tab"), qn(W, "ptab")):
                c.text_chars += 1
                out.append(TextRun("\t", fmt))
            elif tag in (qn(W, "br"), qn(W, "cr")):
                c.text_chars += 1
                out.append(LineBreak())
            elif tag == qn(W, "noBreakHyphen"):
                c.text_chars += 1
                out.append(TextRun("‑", fmt))
            elif tag == qn(W, "softHyphen"):
                c.text_chars += 1
                out.append(TextRun("­", fmt))
            elif tag == qn(W, "sym"):
                c.text_chars += 1
                sym_font = child.get(qn(W, "font"), "")
                try:
                    ch = chr(int(child.get(qn(W, "char"), ""), 16))
                except ValueError:
                    self.ctx.issue("INVALID_SYMBOL", "error", "w:sym with invalid character code")
                    continue
                if font_kind(sym_font) == "symbol":
                    mapped = map_symbol_char(ch)
                    if mapped is not None:
                        c.symbol_chars_mapped += 1
                        out.append(TextRun(mapped, _without_font(fmt)))
                        continue
                    self.ctx.issue("UNMAPPED_SYMBOL_CHAR", "warning", "Symbol-font characters without Unicode mapping")
                else:
                    self.ctx.issue("DINGBAT_FONT_TEXT", "warning", f"symbol from font {sym_font} kept with its font")
                out.append(TextRun(ch, RunFormat(**{**vars(fmt), "font_family": sym_font or None})))
            elif tag == qn(W, "drawing"):
                self.drawing(child, out)
            elif tag == qn(W, "pict"):
                self.vml(child, out)
            elif tag == qn(W, "object"):
                self.ole(child, out)
            elif tag in (qn(W, "footnoteReference"), qn(W, "endnoteReference")):
                note = "footnote" if tag == qn(W, "footnoteReference") else "endnote"
                self.ctx.issue("NOTE_CONTENT_NOT_INCLUDED", "warning",
                               f"{note} reference rendered as its mark; note text is outside the claim")
                if child.get(qn(W, "customMarkFollows")) in ("1", "true", "on"):
                    continue
                if note == "footnote":
                    self.ctx.footnotes_seen += 1
                    out.append(NoteReference(str(self.ctx.footnotes_seen), "footnote"))
                else:
                    self.ctx.endnotes_seen += 1
                    out.append(NoteReference(str(self.ctx.endnotes_seen), "endnote"))
            elif tag == qn(W, "delText"):
                continue
            elif tag == qn(MC, "AlternateContent"):
                chosen = _choose_alternate(child)
                if chosen is not None:
                    fake = etree.Element(qn(W, "r"))
                    if rpr is not None:
                        fake.append(etree.fromstring(etree.tostring(rpr)))
                    for gc in chosen:
                        fake.append(etree.fromstring(etree.tostring(gc)))
                    self.run(fake, pstyle, out)
            elif tag in (qn(W, "separator"), qn(W, "continuationSeparator"), qn(W, "footnoteRef"),
                         qn(W, "endnoteRef"), qn(W, "pgNum")):
                continue
            elif tag == qn(W, "control"):
                self.ctx.issue("ACTIVEX_CONTROL", "error", "ActiveX control cannot be rendered")
            else:
                self._unknown(child)

    # ------------------------------------------------------------------ equations
    def equation(self, om: etree._Element, display: bool) -> Equation:
        c = self.counters
        c.equations += 1
        res = omml_to_mathml(om, display)
        c.text_chars += res.text_chars
        if res.faithful and self.ctx.policy.equation_mode == "mathml":
            c.equations_mathml += 1
            return Equation(display=display, mathml=res.mathml, alt=res.linear, text_chars=res.text_chars)
        if not res.faithful:
            self.ctx.issue("EQUATION_RENDERED_AS_IMAGE", "warning",
                           f"OMML constructs {sorted(res.unsupported)} rendered as image")
        c.equations_image += 1
        c.image_refs += self._image_refs_in(om)
        xml = etree.tostring(om)
        para_xml = (b'<m:oMathPara xmlns:m="' + M.encode() + b'">' + xml + b"</m:oMathPara>") if display else None
        aid = self.ctx.registry.snippet("equation", None if display else xml, self._parts_in(om), None, "crop",
                                        paragraph_xml=para_xml)
        return Equation(display=display, mathml=None, fallback_asset_id=aid, alt=res.linear, text_chars=res.text_chars)

    # ------------------------------------------------------------------ drawings
    def _image_from_blip(self, container: etree._Element, extent, alt, floating, source, out) -> bool:
        blip = container.find(".//" + qn(A, "blip"))
        if blip is None:
            return False
        rid = blip.get(qn(R, "embed"))
        c = self.counters
        c.image_refs += self._image_refs_in(container)
        rel = self.ctx.rels.get(rid or "")
        if not rid or rel is None or rel.external or not rel.part_name:
            self.ctx.issue("LINKED_IMAGE", "error", "image is linked to an external file and is not in the document")
            return True
        svg_part = None
        svg = blip.find(".//" + qn(ASVG, "svgBlip"))
        if svg is not None:
            srel = self.ctx.rels.get(svg.get(qn(R, "embed"), ""))
            if srel is not None and not srel.external:
                svg_part = srel.part_name
        aid = self.ctx.registry.media(rel.part_name, svg_part, extent)
        c.images += 1
        out.append(Image(aid, _emu_px(extent[0]) if extent else None, _emu_px(extent[1]) if extent else None,
                         alt=alt, floating=floating, source=source))  # type: ignore[arg-type]
        return True

    def drawing(self, d: etree._Element, out: list) -> None:
        holder = d.find(qn(WP, "inline"))
        floating = holder is None
        if holder is None:
            holder = d.find(qn(WP, "anchor"))
        if holder is None:
            self._unknown(d)
            return
        ext = holder.find(qn(WP, "extent"))
        extent = (int(ext.get("cx", "0")), int(ext.get("cy", "0"))) if ext is not None else None
        docpr = holder.find(qn(WP, "docPr"))
        alt = (docpr.get("descr") or docpr.get("title") or "") if docpr is not None else ""
        gd = holder.find(f"{qn(A, 'graphic')}/{qn(A, 'graphicData')}")
        if floating:
            self.counters.floating_objects += 1
        if gd is None:
            self._unknown(d)
            return
        first = next((c for c in gd if isinstance(c.tag, str)), None)
        if first is not None and first.tag == qn(PIC, "pic"):
            self._image_from_blip(first, extent, alt, floating, "media", out)
            return
        if first is not None and first.tag in (qn(C, "chart"),) or gd.find(".//" + qn(C, "chart")) is not None:
            self.ctx.issue("CHART", "error", "DrawingML chart cannot be rendered faithfully")
            self.counters.text_chars_removed += len(scan_block(d).text)
            return
        if first is not None and first.tag == qn(DGM, "relIds"):
            self.ctx.issue("SMARTART", "error", "SmartArt diagram cannot be rendered faithfully")
            self.counters.text_chars_removed += len(scan_block(d).text)
            return
        if first is not None and first.tag == qn(WPS, "wsp"):
            txbx = first.find(f"{qn(WPS, 'txbx')}/{qn(W, 'txbxContent')}")
            geom = first.find(f".//{qn(A, 'prstGeom')}")
            simple_box = geom is None or geom.get("prst") in ("rect", "roundRect")
            if txbx is not None and simple_box:
                ln = first.find(f"{qn(WPS, 'spPr')}/{qn(A, 'ln')}")
                bordered = ln is not None and ln.find(qn(A, "noFill")) is None
                self.counters.textboxes += 1
                self.counters.image_refs += 0
                out.append(TextBox(self.blocks(txbx), floating, _emu_px(extent[0]) if extent else None, bordered))
                return
        if first is None or first.tag not in (qn(WPS, "wsp"), qn(WPG, "wgp"), qn(WPC, "wpc")):
            self.ctx.issue("UNSUPPORTED_DRAWING", "error", f"unsupported drawing type {gd.get('uri')}")
            return
        self._snippet(d, "drawing", extent, alt, floating, out)

    def _snippet(self, el, purpose, extent, alt, floating, out) -> None:
        if el.find(".//" + qn(A, "blip")) is not None:
            for blip in el.iter(qn(A, "blip")):
                if not blip.get(qn(R, "embed")):
                    self.ctx.issue("LINKED_IMAGE", "error", "drawing contains an externally linked image")
        self._account_rasterized(el)
        aid = self.ctx.registry.snippet(purpose, etree.tostring(el), self._parts_in(el), extent, "exact")
        self.counters.images += 1
        out.append(Image(aid, _emu_px(extent[0]) if extent else None, _emu_px(extent[1]) if extent else None,
                         alt=alt, floating=floating, source="drawing" if purpose == "drawing" else "vml"))

    @staticmethod
    def _vml_size(style: str | None) -> tuple[int, int] | None:
        if not style:
            return None
        dims = {}
        for m in _STYLE_DIM.finditer(style):
            dims[m.group(1).lower()] = float(m.group(2)) * _UNIT_PT.get((m.group(3) or "pt").lower(), 1.0)
        if "width" in dims and "height" in dims:
            return int(dims["width"] * 12700), int(dims["height"] * 12700)  # pt -> EMU
        return None

    def vml(self, pict: etree._Element, out: list) -> None:
        shapes = [c for c in pict if isinstance(c.tag, str) and c.tag != qn(V, "shapetype")]
        if len(shapes) == 1 and shapes[0].tag in (qn(V, "shape"), qn(V, "rect")):
            shape = shapes[0]
            extent = self._vml_size(shape.get("style"))
            floating = "position:absolute" in (shape.get("style") or "").replace(" ", "")
            textbox = shape.find(f"{qn(V, 'textbox')}/{qn(W, 'txbxContent')}")
            imagedata = shape.find(qn(V, "imagedata"))
            if textbox is not None and imagedata is None:
                self.counters.textboxes += 1
                if floating:
                    self.counters.floating_objects += 1
                bordered = (shape.get("stroked") or "t").lower() not in ("f", "false")
                out.append(TextBox(self.blocks(textbox), floating, _emu_px(extent[0]) if extent else None, bordered))
                return
            if imagedata is not None and textbox is None:
                self._vml_image(imagedata, extent, shape.get("alt") or "", floating, "media", out)
                return
        if not shapes:
            return
        extent = self._vml_size(shapes[0].get("style"))
        self._snippet(pict, "vml", extent, "", False, out)

    def _vml_image(self, imagedata, extent, alt, floating, source, out) -> None:
        rid = imagedata.get(qn(R, "id")) or imagedata.get(qn(R, "pict")) or imagedata.get(qn(O, "relid"))
        rel = self.ctx.rels.get(rid or "")
        self.counters.image_refs += self._image_refs_in(imagedata)
        if rel is None or rel.external or not rel.part_name:
            self.ctx.issue("LINKED_IMAGE", "error", "VML image is linked externally or missing")
            return
        aid = self.ctx.registry.media(rel.part_name, None, extent)
        self.counters.images += 1
        out.append(Image(aid, _emu_px(extent[0]) if extent else None, _emu_px(extent[1]) if extent else None,
                         alt=alt or (imagedata.get(qn(O, "title")) or ""), floating=floating, source=source))

    def ole(self, obj: etree._Element, out: list) -> None:
        ole = obj.find(qn(O, "OLEObject"))
        prog = (ole.get("ProgID") if ole is not None else None) or "OLE object"
        shape = obj.find(qn(V, "shape"))
        imagedata = shape.find(qn(V, "imagedata")) if shape is not None else None
        if imagedata is not None:
            extent = self._vml_size(shape.get("style"))
            self.ctx.issue("OLE_RENDERED_FROM_PREVIEW", "warning", f"{prog} rendered from its stored preview image")
            self._vml_image(imagedata, extent, prog, False, "ole_preview", out)
            return
        drawing = obj.find(".//" + qn(W, "drawing"))
        if drawing is not None:
            self.drawing(drawing, out)
            return
        self.ctx.issue("OLE_WITHOUT_PREVIEW", "error", f"embedded {prog} has no preview image")

    # ------------------------------------------------------------------ tables
    def table(self, tbl: etree._Element) -> Table:
        self.counters.tables += 1
        tblpr = tbl.find(qn(W, "tblPr"))
        tstyle_el = tblpr.find(qn(W, "tblStyle")) if tblpr is not None else None
        tprops = self.ctx.styles.table_props(tstyle_el.get(qn(W, "val")) if tstyle_el is not None else None)
        borders: dict[str, Border] = dict(tprops.borders)
        rtl = bool(tprops.bidi_visual)
        if tblpr is not None:
            borders.update(parse_borders(tblpr.find(qn(W, "tblBorders"))))
            if tblpr.find(qn(W, "bidiVisual")) is not None:
                rtl = tblpr.find(qn(W, "bidiVisual")).get(qn(W, "val"), "1") not in ("0", "false")
        rows_el = list(_flatten(tbl, qn(W, "tr")))
        table = Table(direction="rtl" if rtl else "ltr")
        active: dict[int, TableCell] = {}
        n_rows = len(rows_el)
        for ri, tr in enumerate(rows_el):
            row = TableRow()
            col = 0
            trpr = tr.find(qn(W, "trPr"))
            if trpr is not None and trpr.find(qn(W, "gridBefore")) is not None:
                col += int(trpr.find(qn(W, "gridBefore")).get(qn(W, "val"), "0") or 0)
            cells_el = list(_flatten(tr, qn(W, "tc")))
            for ci, tc in enumerate(cells_el):
                tcpr = tc.find(qn(W, "tcPr"))
                span = 1
                vmerge = None
                hmerge = None
                if tcpr is not None:
                    gs = tcpr.find(qn(W, "gridSpan"))
                    if gs is not None:
                        span = max(1, int(gs.get(qn(W, "val"), "1") or 1))
                    vm = tcpr.find(qn(W, "vMerge"))
                    if vm is not None:
                        vmerge = vm.get(qn(W, "val"), "continue")
                    hm = tcpr.find(qn(W, "hMerge"))
                    if hm is not None:
                        hmerge = hm.get(qn(W, "val"), "continue")
                blocks = self.blocks(tc)
                if vmerge == "continue" and col in active:
                    target = active[col]
                    target.row_span += 1
                    if _has_content(blocks):
                        target.blocks.extend(blocks)
                    col += span
                    continue
                if hmerge == "continue" and row.cells:
                    row.cells[-1].col_span += span
                    if _has_content(blocks):
                        row.cells[-1].blocks.extend(blocks)
                    col += span
                    continue
                cell = TableCell(blocks=blocks, col_span=span)
                self._cell_style(cell, tcpr, borders, ri, n_rows, ci, len(cells_el))
                if vmerge == "restart":
                    active[col] = cell
                else:
                    active.pop(col, None)
                row.cells.append(cell)
                col += span
            table.rows.append(row)
        return table

    def _cell_style(self, cell, tcpr, tborders, ri, n_rows, ci, n_cols) -> None:
        own = parse_borders(tcpr.find(qn(W, "tcBorders"))) if tcpr is not None else {}
        sides = {
            "top": own.get("top") or tborders.get("top" if ri == 0 else "insideH"),
            "bottom": own.get("bottom") or tborders.get("bottom" if ri == n_rows - 1 else "insideH"),
            "start": own.get("start") or tborders.get("start" if ci == 0 else "insideV"),
            "end": own.get("end") or tborders.get("end" if ci == n_cols - 1 else "insideV"),
        }
        for side, b in sides.items():
            if b is None:
                continue
            if b.style in ("nil", "none"):
                cell.borders[side] = None
            else:
                style = {"double": "double", "dotted": "dotted", "dashed": "dashed", "dashSmallGap": "dashed",
                         "dotDash": "dashed", "triple": "double"}.get(b.style, "solid")
                width = max(1.0, round(b.size_eighths / 8.0 * 96 / 72, 2))
                color = "#" + b.color.upper() if b.color and re.fullmatch(r"[0-9A-Fa-f]{6}", b.color) else "#000000"
                cell.borders[side] = CellBorder(style, width, color)
        if tcpr is None:
            return
        shd = tcpr.find(qn(W, "shd"))
        if shd is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", shd.get(qn(W, "fill"), "") or ""):
            fill = shd.get(qn(W, "fill")).upper()
            if fill != "FFFFFF":
                cell.background = "#" + fill
        va = tcpr.find(qn(W, "vAlign"))
        if va is not None:
            cell.vertical_align = {"center": "middle", "bottom": "bottom", "top": "top"}.get(va.get(qn(W, "val"), ""))
        tcw = tcpr.find(qn(W, "tcW"))
        if tcw is not None and tcw.get(qn(W, "type")) == "dxa":
            try:
                cell.width_pt = round(int(tcw.get(qn(W, "w"), "0")) / 20.0, 1) or None
            except ValueError:
                pass


def _flatten(parent: etree._Element, tag: str):
    """Yield ``tag`` children, looking through content controls / customXml wrappers."""
    for child in parent:
        if child.tag == tag:
            yield child
        elif child.tag == qn(W, "sdt"):
            content = child.find(qn(W, "sdtContent"))
            if content is not None:
                yield from _flatten(content, tag)
        elif child.tag == qn(W, "customXml"):
            yield from _flatten(child, tag)


def _has_content(blocks) -> bool:
    for b in blocks:
        if isinstance(b, Table):
            return True
        if any(not (isinstance(i, TextRun) and not i.text.strip()) for i in b.inlines):
            return True
    return False


def _without_font(fmt: RunFormat) -> RunFormat:
    return RunFormat(**{**vars(fmt), "font_family": None})


def _merge_runs(items: list) -> list:
    out: list = []
    for it in items:
        if isinstance(it, TextRun) and out and isinstance(out[-1], TextRun) and out[-1].fmt == it.fmt:
            out[-1] = TextRun(out[-1].text + it.text, it.fmt)
        else:
            out.append(it)
    return out
