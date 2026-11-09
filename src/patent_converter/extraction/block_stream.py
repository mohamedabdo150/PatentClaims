"""Top-level block stream shared by the analyzer and the extractor.

Both passes must see exactly the same blocks in the same order, so the
expansion rules live here:

* ``w:sdt`` / ``w:customXml`` at body level are expanded to their content.
* A table that contains claim markers (section start / separator) is a *layout
  container* (typical for documents converted from PDF, where every claim sits in a
  table row). Its cells are linearized in document order (row by row, cell by cell)
  so that markers inside cells can delimit claims. Ordinary data tables are kept
  as tables.
* Row rule: in such a row, a cell containing only a claim number labels the text of
  the *same row*. If the row ends with separator markers, the number is placed
  before them (otherwise the number would fall into the next claim). The rule only
  moves number-only cells within their own row; the numbers are still validated
  (missing / ambiguous numbers remain errors).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator

from lxml import etree

from ..analyzer.block_scanner import paragraph_segments, paragraph_text
from ..config.claim_patterns import CompiledLanguagePatterns
from ..ooxml.namespaces import W, qn

_SDT = qn(W, "sdt")
_CUSTOM = qn(W, "customXml")
_TBL = qn(W, "tbl")
_P = qn(W, "p")


def _children_rows(parent: etree._Element, tag: str) -> Iterator[etree._Element]:
    for child in parent:
        if child.tag == tag:
            yield child
        elif child.tag == _SDT:
            content = child.find(qn(W, "sdtContent"))
            if content is not None:
                yield from _children_rows(content, tag)
        elif child.tag == _CUSTOM:
            yield from _children_rows(child, tag)


def table_has_marker(tbl: etree._Element, patterns: CompiledLanguagePatterns) -> bool:
    return any(patterns.is_marker(paragraph_text(p)) for p in tbl.iter(_P))


def iter_blocks(el: etree._Element, patterns: CompiledLanguagePatterns | None,
                stats: dict | None = None) -> Iterator[etree._Element]:
    tag = el.tag
    if tag in (_SDT, _CUSTOM):
        container = el.find(qn(W, "sdtContent")) if tag == _SDT else el
        if container is not None:
            for child in container:
                if isinstance(child.tag, str) and child.tag != qn(W, "sdtPr"):
                    yield from iter_blocks(child, patterns, stats)
        return
    if (tag == _TBL and patterns is not None and patterns.config.linearize_marker_tables
            and table_has_marker(el, patterns)):
        if stats is not None:
            stats["linearized_tables"] = stats.get("linearized_tables", 0) + 1
        for tr in _children_rows(el, qn(W, "tr")):
            yield from _linearize_row(tr, patterns, stats)
        return
    yield el


def _is_number_only(p: etree._Element, patterns: CompiledLanguagePatterns) -> bool:
    text = patterns.prepare(paragraph_text(p))
    if not text.strip():
        return False
    return any(rx.match(text) and rx.match(text).end() >= len(text.rstrip()) for rx in patterns.numbers)


def _linearize_row(tr: etree._Element, patterns: CompiledLanguagePatterns, stats: dict | None):
    cells = []
    for tc in _children_rows(tr, qn(W, "tc")):
        blocks = []
        for child in tc:
            if isinstance(child.tag, str) and child.tag != qn(W, "tcPr"):
                blocks.extend(iter_blocks(child, patterns, stats))
        cells.append(blocks)

    def is_marker(b: etree._Element) -> bool:
        return b.tag == _P and patterns.separator is not None and bool(
            patterns.separator.fullmatch(patterns.prepare(paragraph_text(b)).strip()))

    def is_empty(b: etree._Element) -> bool:
        return b.tag == _P and not paragraph_text(b).strip() and not b.findall(".//" + qn(W, "drawing"))

    number_cells = [i for i, c in enumerate(cells)
                    if c and all(is_empty(b) or (b.tag == _P and _is_number_only(b, patterns)) for b in c)
                    and any(not is_empty(b) for b in c)]
    if not number_cells or len(number_cells) == len(cells):
        for c in cells:
            yield from c
        return
    numbers = [b for i in number_cells for b in cells[i]]
    rest = [b for i, c in enumerate(cells) if i not in number_cells for b in c]
    cut = len(rest)
    while cut > 0 and (is_marker(rest[cut - 1]) or is_empty(rest[cut - 1])):
        cut -= 1
    if any(is_marker(b) for b in rest[cut:]):
        if stats is not None:
            stats["row_numbers_moved"] = stats.get("row_numbers_moved", 0) + 1
        yield from rest[:cut]
        yield from numbers
        yield from rest[cut:]
    else:
        for c in cells:
            yield from c


def strip_markers(p: etree._Element, regex: re.Pattern[str]) -> tuple[etree._Element, int, bool]:
    """Remove marker text (possibly split across runs) from a copy of a paragraph.

    Returns (paragraph copy, characters removed, clean). ``clean`` is False when a
    marker overlaps non-text content (tabs, breaks, symbols); such characters are kept.
    """
    text = paragraph_text(p)
    spans = [(m.start(), m.end()) for m in regex.finditer(text)]
    if not spans:
        return p, 0, True
    dup = copy.deepcopy(p)
    segs = paragraph_segments(dup)
    removed = 0
    clean = True
    pos = 0
    for el, seg in segs:
        seg_start, seg_end = pos, pos + len(seg)
        pos = seg_end
        cut = [(max(a, seg_start) - seg_start, min(b, seg_end) - seg_start) for a, b in spans if a < seg_end and b > seg_start]
        if not cut:
            continue
        if el is None:
            clean = False
            continue
        keep, last = [], 0
        for a, b in cut:
            keep.append(seg[last:a])
            last = b
            removed += b - a
        keep.append(seg[last:])
        el.text = "".join(keep)
    return dup, removed, clean
