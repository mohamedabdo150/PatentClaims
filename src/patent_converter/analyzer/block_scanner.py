"""Structural scan of a single top-level body block (w:p, w:tbl, ...).

The scanner walks the element subtree once (iteratively, so hostile nesting
cannot overflow the stack) and records:

* counts of every content structure relevant to conversion,
* relationship references (images, OLE, charts, hyperlinks, ...),
* visible text (for Unicode diagnostics and size estimates),
* WordprocessingML/OMML elements the converter does not know.

``mc:AlternateContent`` is resolved like Word does: the first ``mc:Choice``
whose required namespaces are understood is scanned, otherwise the
``mc:Fallback``. The branch not taken is never counted, so a modern text box
with a VML fallback is not reported twice.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from ..ooxml.namespaces import (
    A, A14, ASVG, C, DGM, M, MC, O, PIC, R, V, W, W14, W15, W16SE, WP, WP14, WPC, WPG, WPS, prefixed, qn,
)

SYMBOL_FONTS = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings", "mt extra", "zapf dingbats"}
UNDERSTOOD_NAMESPACES = {W, W14, W15, W16SE, WPS, WPG, WPC, WP14, A, A14, ASVG, PIC, M, V, O, WP, C, DGM}

# WordprocessingML content-level elements the converter knows (property
# containers *Pr are skipped entirely by the unknown-element check).
KNOWN_W = {
    "p", "r", "t", "tab", "br", "cr", "sym", "drawing", "pict", "object", "fldSimple", "fldChar", "instrText",
    "delText", "delInstrText", "hyperlink", "bookmarkStart", "bookmarkEnd", "proofErr", "lastRenderedPageBreak",
    "noBreakHyphen", "softHyphen", "tbl", "tblGrid", "gridCol", "tr", "tc", "sdt", "sdtContent", "sectPr",
    "ins", "del", "moveFrom", "moveTo", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
    "moveToRangeEnd", "commentRangeStart", "commentRangeEnd", "commentReference", "footnoteReference",
    "endnoteReference", "txbxContent", "smartTag", "customXml", "permStart", "permEnd", "ptab", "annotationRef",
    "footnoteRef", "endnoteRef", "separator", "continuationSeparator", "pgNum", "altChunk", "dir", "bdo",
    "cellIns", "cellDel", "cellMerge", "control", "contentPart", "ruby", "rt", "rubyBase", "dayShort",
    "monthShort", "yearShort", "dayLong", "monthLong", "yearLong", "customXmlInsRangeStart",
    "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd", "subDoc",
}
KNOWN_M = {
    "oMathPara", "oMath", "r", "t", "f", "num", "den", "sSup", "sSub", "sSubSup", "sPre", "sup", "sub", "e",
    "rad", "deg", "nary", "d", "m", "mr", "acc", "bar", "box", "borderBox", "eqArr", "func", "fName", "groupChr",
    "limLow", "limUpp", "lim", "phant",
}
OMML_CONSTRUCTS = {
    "f", "sSup", "sSub", "sSubSup", "sPre", "rad", "nary", "d", "m", "acc", "bar", "box", "borderBox", "eqArr",
    "func", "groupChr", "limLow", "limUpp", "phant",
}
VML_SHAPES = {"shape", "rect", "oval", "line", "polyline", "roundrect", "arc", "curve", "shapetype"}
TRACKED = {"ins": "insertions", "del": "deletions", "moveFrom": "moves", "moveTo": "moves",
           "rPrChange": "format_changes", "pPrChange": "format_changes", "tblPrChange": "format_changes",
           "tcPrChange": "format_changes", "trPrChange": "format_changes", "sectPrChange": "format_changes",
           "cellIns": "cell_changes", "cellDel": "cell_changes", "cellMerge": "cell_changes"}

_T = qn(W, "t")
_MT = qn(M, "t")
_SKIP_TEXT = {qn(W, "delText"), qn(W, "instrText"), qn(W, "delInstrText")}
_R_ATTRS = ("embed", "link", "id", "pict", "dm", "lo", "qs", "cs")


@dataclass
class RelReference:
    rel_id: str
    attribute: str
    element: str
    linked: bool = False  # r:link (external content)


@dataclass
class BlockStats:
    counts: Counter = field(default_factory=Counter)
    omml_constructs: Counter = field(default_factory=Counter)
    field_types: Counter = field(default_factory=Counter)
    symbol_fonts: Counter = field(default_factory=Counter)
    unknown_elements: Counter = field(default_factory=Counter)
    ole_progids: Counter = field(default_factory=Counter)
    references: list[RelReference] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    def merge(self, other: "BlockStats") -> None:
        self.counts.update(other.counts)
        self.omml_constructs.update(other.omml_constructs)
        self.field_types.update(other.field_types)
        self.symbol_fonts.update(other.symbol_fonts)
        self.unknown_elements.update(other.unknown_elements)
        self.ole_progids.update(other.ole_progids)


def _choose_alternate(ac: etree._Element) -> etree._Element | None:
    for child in ac:
        if child.tag == qn(MC, "Choice"):
            requires = (child.get("Requires") or "").split()
            nsmap = child.nsmap
            if all(nsmap.get(prefix) in UNDERSTOOD_NAMESPACES for prefix in requires):
                return child
        elif child.tag == qn(MC, "Fallback"):
            return child
    return None


def _run_fonts(rpr: etree._Element) -> list[str]:
    fonts = rpr.find(qn(W, "rFonts"))
    if fonts is None:
        return []
    return [v for k, v in fonts.attrib.items() if etree.QName(k).localname in ("ascii", "hAnsi", "cs", "eastAsia")]


def scan_block(root: etree._Element) -> BlockStats:
    stats = BlockStats()
    c = stats.counts
    # Stack entries: (element, check_unknown, table_depth)
    stack: list[tuple[etree._Element, bool, int]] = [(root, True, 0)]
    instr_buffer: list[str] | None = None
    while stack:
        el, check_unknown, tbl_depth = stack.pop()
        tag = el.tag
        if not isinstance(tag, str):  # comments / PIs
            continue
        ns, _, local = tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
        child_check = check_unknown
        child_tbl_depth = tbl_depth

        if ns == MC and local == "AlternateContent":
            c["alternate_content"] += 1
            chosen = _choose_alternate(el)
            if chosen is not None:
                if chosen.tag == qn(MC, "Fallback"):
                    c["alternate_content_fallback_used"] += 1
                for child in reversed(chosen):
                    stack.append((child, check_unknown, tbl_depth))
            else:
                c["alternate_content_unresolved"] += 1
            continue

        # Relationship references on any element
        for attr_name, value in el.attrib.items():
            if attr_name.startswith("{" + R + "}"):
                a_local = attr_name[len(R) + 2:]
                if a_local in _R_ATTRS:
                    stats.references.append(RelReference(value, a_local, prefixed(tag), linked=(a_local == "link")))
            elif ns == V and attr_name == qn(O, "relid"):
                stats.references.append(RelReference(value, "o:relid", prefixed(tag)))

        if ns == W:
            if local.endswith("Pr") or local.endswith("PrEx") or local.endswith("PrChange"):
                if local in TRACKED:
                    c["tracked_" + TRACKED[local]] += 1
                if local == "rPr":
                    fonts = _run_fonts(el)
                    sym = {f for f in fonts if f.lower() in SYMBOL_FONTS}
                    if sym:
                        c["symbol_font_runs"] += 1
                        stats.symbol_fonts.update(sym)
                    if el.find(qn(W, "rtl")) is not None:
                        c["rtl_runs"] += 1
                    va = el.find(qn(W, "vertAlign"))
                    if va is not None:
                        c["vert_align_" + va.get(qn(W, "val"), "baseline")] += 1
                elif local == "pPr":
                    if el.find(qn(W, "bidi")) is not None:
                        c["rtl_paragraphs"] += 1
                    if el.find(qn(W, "numPr")) is not None:
                        c["numbered_paragraphs"] += 1
                    if el.find(qn(W, "sectPr")) is not None:
                        c["section_breaks"] += 1
                elif local == "tcPr":
                    gs = el.find(qn(W, "gridSpan"))
                    if gs is not None and gs.get(qn(W, "val"), "1") not in ("0", "1"):
                        c["merged_cells_horizontal"] += 1
                    if el.find(qn(W, "vMerge")) is not None:
                        c["merged_cells_vertical"] += 1
                    if el.find(qn(W, "hMerge")) is not None:
                        c["merged_cells_legacy_horizontal"] += 1
                    if el.find(qn(W, "textDirection")) is not None:
                        c["cells_text_direction"] += 1
                elif local == "tblPr":
                    if el.find(qn(W, "bidiVisual")) is not None:
                        c["rtl_tables"] += 1
                continue  # property subtree fully handled
            if local == "p":
                c["paragraphs"] += 1
            elif local == "r":
                c["runs"] += 1
            elif local == "t":
                if el.text:
                    stats.text_parts.append(el.text)
            elif local in ("tab", "ptab"):
                stats.text_parts.append("\t")
            elif local in ("br", "cr"):
                btype = el.get(qn(W, "type"), "textWrapping")
                c[f"breaks_{btype}"] += 1
                stats.text_parts.append("\n")
            elif local == "noBreakHyphen":
                stats.text_parts.append("‑")
            elif local == "softHyphen":
                stats.text_parts.append("­")
            elif local == "sym":
                c["symbols"] += 1
                font = el.get(qn(W, "font"), "")
                stats.symbol_fonts[font or "(none)"] += 1
                code = el.get(qn(W, "char"), "")
                try:
                    stats.text_parts.append(chr(int(code, 16)))
                except ValueError:
                    c["symbols_invalid_char"] += 1
            elif local == "tbl":
                c["tables"] += 1
                if tbl_depth > 0:
                    c["nested_tables"] += 1
                child_tbl_depth = tbl_depth + 1
                c["max_table_nesting"] = max(c["max_table_nesting"], child_tbl_depth)
            elif local == "tr":
                c["table_rows"] += 1
            elif local == "tc":
                c["table_cells"] += 1
            elif local == "drawing":
                c["drawings"] += 1
                child_check = False
            elif local == "pict":
                c["vml_pict"] += 1
                child_check = False
            elif local == "object":
                c["w_objects"] += 1
                if el.find(".//" + qn(V, "imagedata")) is None and el.find(".//" + qn(A, "blip")) is None:
                    c["objects_without_preview"] += 1
                child_check = False
            elif local == "txbxContent":
                c["textboxes"] += 1
                child_check = True
            elif local == "hyperlink":
                c["hyperlinks"] += 1
            elif local == "fldSimple":
                c["fields_simple"] += 1
                instr = (el.get(qn(W, "instr")) or "").split()
                stats.field_types[(instr[0].upper() if instr else "(empty)")] += 1
            elif local == "fldChar":
                ftype = el.get(qn(W, "fldCharType"))
                if ftype == "begin":
                    c["fields_complex"] += 1
                    instr_buffer = []
                elif ftype in ("separate", "end") and instr_buffer is not None:
                    words = "".join(instr_buffer).split()
                    stats.field_types[(words[0].upper() if words else "(empty)")] += 1
                    instr_buffer = None
            elif local == "instrText":
                if instr_buffer is not None and el.text:
                    instr_buffer.append(el.text)
            elif local in TRACKED:
                c["tracked_" + TRACKED[local]] += 1
            elif local == "commentReference":
                c["comment_references"] += 1
            elif local == "footnoteReference":
                c["footnote_references"] += 1
            elif local == "endnoteReference":
                c["endnote_references"] += 1
            elif local == "altChunk":
                c["altchunks"] += 1
            elif local == "sdt":
                c["content_controls"] += 1
            elif local in ("dir", "bdo"):
                c["bidi_overrides"] += 1
            elif local == "ruby":
                c["ruby"] += 1
            elif local == "subDoc":
                c["subdocuments"] += 1
            elif local == "control":
                c["activex_controls"] += 1
            if check_unknown and local not in KNOWN_W:
                stats.unknown_elements[prefixed(tag)] += 1
        elif ns == M:
            if local.endswith("Pr"):
                continue
            if local == "oMath":
                c["equations"] += 1
            elif local == "oMathPara":
                c["equation_paragraphs"] += 1
            elif local == "t":
                if el.text:
                    stats.text_parts.append(el.text)
            if local in OMML_CONSTRUCTS:
                stats.omml_constructs[local] += 1
            if check_unknown and local not in KNOWN_M:
                stats.unknown_elements[prefixed(tag)] += 1
        elif ns == WP:
            if local == "inline":
                c["drawing_inline"] += 1
            elif local == "anchor":
                c["drawing_anchor"] += 1
        elif ns == PIC and local == "pic":
            c["pictures"] += 1
        elif ns == A and local == "blip":
            c["blips"] += 1
            if el.get(qn(R, "link")) and not el.get(qn(R, "embed")):
                c["linked_only_images"] += 1
        elif ns == ASVG and local == "svgBlip":
            c["svg_blips"] += 1
        elif ns == WPG and local == "wgp":
            c["drawing_groups"] += 1
        elif ns == WPC and local == "wpc":
            c["drawing_canvases"] += 1
        elif ns == WPS and local == "wsp":
            c["drawing_shapes"] += 1
        elif ns == C and local == "chart":
            c["charts"] += 1
        elif ns == DGM and local == "relIds":
            c["smartart"] += 1
        elif ns == V:
            if local in VML_SHAPES:
                c["vml_shapes"] += 1
            elif local == "group":
                c["vml_groups"] += 1
            elif local == "imagedata":
                c["vml_imagedata"] += 1
            elif local == "textbox":
                c["vml_textboxes"] += 1
        elif ns == O and local == "OLEObject":
            c["ole_objects"] += 1
            c["ole_" + (el.get("Type") or "Embed").lower()] += 1
            stats.ole_progids[el.get("ProgID") or "(unknown)"] += 1
        elif check_unknown and ns not in UNDERSTOOD_NAMESPACES and ns != MC:
            stats.unknown_elements[prefixed(tag)] += 1

        for child in reversed(el):
            stack.append((child, child_check, child_tbl_depth))
    return stats


def paragraph_segments(p: etree._Element) -> list[tuple[etree._Element | None, str]]:
    """Logical text of a paragraph as (source w:t/m:t element or None, text) segments.

    Excludes text boxes, deleted text, field instructions and untaken
    AlternateContent branches. Field *results* (e.g. SEQ numbers) are included.
    """
    out: list[tuple[etree._Element | None, str]] = []
    stack: list[etree._Element] = [p]
    txbx = qn(W, "txbxContent")
    while stack:
        el = stack.pop()
        tag = el.tag
        if not isinstance(tag, str) or tag in _SKIP_TEXT or tag == txbx:
            continue
        if tag == qn(MC, "AlternateContent"):
            chosen = _choose_alternate(el)
            if chosen is not None:
                stack.extend(reversed(chosen))
            continue
        if tag == _T or tag == _MT:
            if el.text:
                out.append((el, el.text))
        elif tag in (qn(W, "tab"), qn(W, "ptab")):
            out.append((None, "\t"))
        elif tag in (qn(W, "br"), qn(W, "cr")):
            out.append((None, "\n"))
        elif tag == qn(W, "noBreakHyphen"):
            out.append((None, "\u2011"))
        elif tag == qn(W, "sym"):
            try:
                out.append((None, chr(int(el.get(qn(W, "char"), ""), 16))))
            except ValueError:
                pass
        elif tag.endswith("Pr") and tag.startswith("{" + W + "}"):
            continue
        stack.extend(reversed(el))
    return out


def paragraph_text(p: etree._Element) -> str:
    return "".join(t for _el, t in paragraph_segments(p))
