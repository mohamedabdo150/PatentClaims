"""Effective paragraph / run / table formatting from styles.xml.

Resolution order (later wins): docDefaults -> style chain (basedOn, root first)
-> direct formatting. Only properties that affect the rendered appearance or
meaning of claims are resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lxml import etree

from .namespaces import W, qn

_VAL = qn(W, "val")


def _on(el: etree._Element | None) -> bool | None:
    """OOXML on/off property: present without val => True."""
    if el is None:
        return None
    v = el.get(_VAL)
    return v not in ("0", "false", "off", "none")


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


@dataclass
class RunProps:
    bold: bool | None = None
    bold_cs: bool | None = None
    italic: bool | None = None
    italic_cs: bool | None = None
    underline: str | None = None
    strike: bool | None = None
    dstrike: bool | None = None
    vert_align: str | None = None
    font_ascii: str | None = None
    font_cs: str | None = None
    font_east_asia: str | None = None
    size: int | None = None        # half-points
    size_cs: int | None = None
    color: str | None = None
    highlight: str | None = None
    shading: str | None = None
    rtl: bool | None = None
    cs: bool | None = None
    caps: bool | None = None
    small_caps: bool | None = None
    vanish: bool | None = None
    position: int | None = None    # half-points raise/lower

    def merged(self, other: "RunProps") -> "RunProps":
        out = replace(self)
        for k, v in vars(other).items():
            if v is not None:
                setattr(out, k, v)
        return out


@dataclass
class ParaProps:
    jc: str | None = None
    bidi: bool | None = None
    ind_start: int | None = None
    ind_end: int | None = None
    ind_first: int | None = None
    ind_hanging: int | None = None
    space_before: int | None = None
    space_after: int | None = None
    line: int | None = None
    line_rule: str | None = None

    def merged(self, other: "ParaProps") -> "ParaProps":
        out = replace(self)
        for k, v in vars(other).items():
            if v is not None:
                setattr(out, k, v)
        return out


@dataclass
class Border:
    style: str
    size_eighths: int
    color: str | None


@dataclass
class TableProps:
    borders: dict[str, Border] = field(default_factory=dict)
    bidi_visual: bool | None = None
    cell_shading: str | None = None


def parse_rpr(rpr: etree._Element | None) -> RunProps:
    p = RunProps()
    if rpr is None:
        return p
    g = rpr.find
    p.bold, p.bold_cs = _on(g(qn(W, "b"))), _on(g(qn(W, "bCs")))
    p.italic, p.italic_cs = _on(g(qn(W, "i"))), _on(g(qn(W, "iCs")))
    u = g(qn(W, "u"))
    if u is not None:
        p.underline = u.get(_VAL, "single")
    p.strike, p.dstrike = _on(g(qn(W, "strike"))), _on(g(qn(W, "dstrike")))
    va = g(qn(W, "vertAlign"))
    if va is not None:
        p.vert_align = va.get(_VAL)
    f = g(qn(W, "rFonts"))
    if f is not None:
        p.font_ascii = f.get(qn(W, "ascii")) or f.get(qn(W, "hAnsi"))
        p.font_cs = f.get(qn(W, "cs"))
        p.font_east_asia = f.get(qn(W, "eastAsia"))
    sz, szcs = g(qn(W, "sz")), g(qn(W, "szCs"))
    p.size = _int(sz.get(_VAL)) if sz is not None else None
    p.size_cs = _int(szcs.get(_VAL)) if szcs is not None else None
    col = g(qn(W, "color"))
    if col is not None and col.get(_VAL) and col.get(_VAL) != "auto":
        p.color = col.get(_VAL)
    hl = g(qn(W, "highlight"))
    if hl is not None and hl.get(_VAL) not in (None, "none"):
        p.highlight = hl.get(_VAL)
    shd = g(qn(W, "shd"))
    if shd is not None and shd.get(qn(W, "fill")) not in (None, "auto"):
        p.shading = shd.get(qn(W, "fill"))
    p.rtl, p.cs = _on(g(qn(W, "rtl"))), _on(g(qn(W, "cs")))
    p.caps, p.small_caps = _on(g(qn(W, "caps"))), _on(g(qn(W, "smallCaps")))
    p.vanish = _on(g(qn(W, "vanish")))
    pos = g(qn(W, "position"))
    p.position = _int(pos.get(_VAL)) if pos is not None else None
    return p


def parse_ppr(ppr: etree._Element | None) -> ParaProps:
    p = ParaProps()
    if ppr is None:
        return p
    g = ppr.find
    jc = g(qn(W, "jc"))
    if jc is not None:
        p.jc = jc.get(_VAL)
    p.bidi = _on(g(qn(W, "bidi")))
    ind = g(qn(W, "ind"))
    if ind is not None:
        a = lambda *names: next((_int(ind.get(qn(W, n))) for n in names if ind.get(qn(W, n)) is not None), None)  # noqa: E731
        p.ind_start = a("start", "left")
        p.ind_end = a("end", "right")
        p.ind_first = a("firstLine")
        p.ind_hanging = a("hanging")
    sp = g(qn(W, "spacing"))
    if sp is not None:
        p.space_before = _int(sp.get(qn(W, "before")))
        p.space_after = _int(sp.get(qn(W, "after")))
        p.line = _int(sp.get(qn(W, "line")))
        p.line_rule = sp.get(qn(W, "lineRule"))
    return p


def parse_borders(container: etree._Element | None) -> dict[str, Border]:
    out: dict[str, Border] = {}
    if container is None:
        return out
    for child in container:
        if not isinstance(child.tag, str):
            continue
        side = etree.QName(child).localname
        side = {"left": "start", "right": "end"}.get(side, side)
        val = child.get(_VAL, "single")
        out[side] = Border(val, _int(child.get(qn(W, "sz"))) or 4, child.get(qn(W, "color")))
    return out


@dataclass
class _Style:
    sid: str
    stype: str
    based_on: str | None
    ppr: ParaProps
    rpr: RunProps
    tbl: TableProps


class StyleResolver:
    def __init__(self) -> None:
        self.styles: dict[str, _Style] = {}
        self.default_rpr = RunProps()
        self.default_ppr = ParaProps()
        self.default_para_style: str | None = None
        self.default_table_style: str | None = None
        self.default_char_style: str | None = None
        self._cache_run: dict[tuple, RunProps] = {}
        self._cache_para: dict[str | None, ParaProps] = {}

    def load(self, root: etree._Element | None) -> None:
        if root is None:
            return
        dd = root.find(qn(W, "docDefaults"))
        if dd is not None:
            self.default_rpr = parse_rpr(dd.find(f"{qn(W, 'rPrDefault')}/{qn(W, 'rPr')}"))
            self.default_ppr = parse_ppr(dd.find(f"{qn(W, 'pPrDefault')}/{qn(W, 'pPr')}"))
        for st in root.iter(qn(W, "style")):
            sid = st.get(qn(W, "styleId"), "")
            stype = st.get(qn(W, "type"), "paragraph")
            based = st.find(qn(W, "basedOn"))
            tblpr = st.find(qn(W, "tblPr"))
            tbl = TableProps(borders=parse_borders(tblpr.find(qn(W, "tblBorders")) if tblpr is not None else None),
                             bidi_visual=_on(tblpr.find(qn(W, "bidiVisual"))) if tblpr is not None else None)
            self.styles[sid] = _Style(sid, stype, based.get(_VAL) if based is not None else None,
                                      parse_ppr(st.find(qn(W, "pPr"))), parse_rpr(st.find(qn(W, "rPr"))), tbl)
            if _on(st.find(qn(W, "default"))) or st.get(qn(W, "default")) in ("1", "true"):
                if stype == "paragraph":
                    self.default_para_style = sid
                elif stype == "table":
                    self.default_table_style = sid
                elif stype == "character":
                    self.default_char_style = sid

    def _chain(self, sid: str | None) -> list[_Style]:
        chain: list[_Style] = []
        seen: set[str] = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            chain.append(self.styles[sid])
            sid = self.styles[sid].based_on
        return list(reversed(chain))

    def paragraph_props(self, pstyle: str | None, direct: ParaProps) -> ParaProps:
        key = pstyle or self.default_para_style
        if key not in self._cache_para:
            props = self.default_ppr
            for s in self._chain(key):
                props = props.merged(s.ppr)
            self._cache_para[key] = props
        return self._cache_para[key].merged(direct)

    def run_props(self, pstyle: str | None, rstyle: str | None, direct: RunProps) -> RunProps:
        key = (pstyle or self.default_para_style, rstyle)
        if key not in self._cache_run:
            props = self.default_rpr
            for s in self._chain(key[0]):
                props = props.merged(s.rpr)
            for s in self._chain(rstyle):
                props = props.merged(s.rpr)
            self._cache_run[key] = props
        return self._cache_run[key].merged(direct)

    def baseline_run(self) -> RunProps:
        return self.run_props(None, None, RunProps())

    def table_props(self, tstyle: str | None) -> TableProps:
        props = TableProps()
        for s in self._chain(tstyle or self.default_table_style):
            props.borders.update(s.tbl.borders)
            if s.tbl.bidi_visual is not None:
                props.bidi_visual = s.tbl.bidi_visual
        return props
