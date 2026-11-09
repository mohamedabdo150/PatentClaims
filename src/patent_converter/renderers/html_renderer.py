"""Canonical model -> CKEditor-compatible HTML skeleton.

Images are emitted as ``data:<mime>;base64,@@A<id>@@`` placeholders that the
writer replaces by streaming each asset's Base64 bytes from disk. Output uses
semantic tags (p, strong, em, u, s, sub, sup, table, a, img), ``dir``/``lang``
for bidirectional text, a small set of controlled ``pc-`` classes and inline
CSS only where it changes appearance. No scripts, no external resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from ..images.assets import AssetRegistry
from ..models.document_model import (
    CellBorder, Claim, DirSpan, Equation, Hyperlink, Image, LineBreak, NoteReference, Paragraph, RunFormat, Table,
    TextBox, TextRun,
)

TAB = '<span style="white-space:pre">\t</span>'


def _t(text: str) -> str:
    return escape(text, quote=False)


def _a(value: str) -> str:
    return escape(value, quote=True)


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


@dataclass
class RenderCounts:
    text_chars: int = 0
    images: int = 0
    mathml: int = 0
    tables: int = 0
    missing_assets: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {"text_chars": self.text_chars, "images": self.images, "mathml": self.mathml, "tables": self.tables}


class HtmlRenderer:
    def __init__(self, registry: AssetRegistry, wrapper_class: str = "pc-claim"):
        self.registry = registry
        self.wrapper_class = wrapper_class

    def render_claim(self, claim: Claim) -> tuple[str, RenderCounts]:
        self.counts = RenderCounts()
        self.base_dir = "rtl" if claim.language == "ar" else "ltr"
        body = "".join(self.block(b, self.base_dir) for b in claim.blocks)
        html = f'<div class="{self.wrapper_class}" dir="{self.base_dir}" lang="{claim.language}">{body}</div>'
        return html, self.counts

    # ------------------------------------------------------------------ blocks
    def block(self, b, ctx_dir: str) -> str:
        if isinstance(b, Paragraph):
            return self.paragraph(b, ctx_dir)
        return self.table(b, ctx_dir)

    def paragraph(self, p: Paragraph, ctx_dir: str) -> str:
        f = p.fmt
        has_box = any(isinstance(i, TextBox) for i in p.inlines)
        tag = "div" if has_box else "p"
        attrs = ""
        if f.direction != ctx_dir:
            attrs += f' dir="{f.direction}"'
        styles = []
        if f.alignment:
            styles.append(f"text-align:{f.alignment}")
        start, end = ("margin-right", "margin-left") if f.direction == "rtl" else ("margin-left", "margin-right")
        if f.margin_start_pt:
            styles.append(f"{start}:{_num(f.margin_start_pt)}pt")
        if f.margin_end_pt:
            styles.append(f"{end}:{_num(f.margin_end_pt)}pt")
        if f.text_indent_pt:
            styles.append(f"text-indent:{_num(f.text_indent_pt)}pt")
        if f.space_before_pt:
            styles.append(f"margin-top:{_num(f.space_before_pt)}pt")
        if f.space_after_pt is not None:
            styles.append(f"margin-bottom:{_num(f.space_after_pt)}pt")
        if f.line_height:
            styles.append(f"line-height:{_num(f.line_height)}")
        if styles:
            attrs += f' style="{_a(";".join(styles))}"'
        inner = ""
        if f.numbering_label:
            inner += f'<span class="pc-label">{_t(f.numbering_label)}</span>{TAB}'
        inner += self.inlines(p.inlines, f.direction)
        if not inner:
            inner = "<br>"
        return f"<{tag}{attrs}>{inner}</{tag}>"

    def table(self, t: Table, ctx_dir: str) -> str:
        self.counts.tables += 1
        attrs = ' class="pc-table"'
        if t.direction != ctx_dir:
            attrs += f' dir="{t.direction}"'
        attrs += ' style="border-collapse:collapse"'
        rows = []
        for row in t.rows:
            cells = []
            for c in row.cells:
                a = ""
                if c.col_span > 1:
                    a += f' colspan="{c.col_span}"'
                if c.row_span > 1:
                    a += f' rowspan="{c.row_span}"'
                st = []
                phys = {"top": "top", "bottom": "bottom",
                        "start": "right" if t.direction == "rtl" else "left",
                        "end": "left" if t.direction == "rtl" else "right"}
                for side in ("top", "end", "bottom", "start"):
                    if side in c.borders:
                        st.append(f"border-{phys[side]}:{self._border(c.borders[side])}")
                if c.background:
                    st.append(f"background-color:{c.background}")
                if c.vertical_align:
                    st.append(f"vertical-align:{c.vertical_align}")
                if c.width_pt:
                    st.append(f"width:{_num(c.width_pt)}pt")
                if st:
                    a += f' style="{_a(";".join(st))}"'
                body = "".join(self.block(b, t.direction) for b in c.blocks) or "<br>"
                cells.append(f"<td{a}>{body}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return f"<table{attrs}><tbody>{''.join(rows)}</tbody></table>"

    @staticmethod
    def _border(b: CellBorder | None) -> str:
        if b is None:
            return "none"
        return f"{_num(b.width_px)}px {b.style} {b.color}"

    # ------------------------------------------------------------------ inlines
    def inlines(self, items, ctx_dir: str) -> str:
        return "".join(self.inline(i, ctx_dir) for i in items)

    def inline(self, it, ctx_dir: str) -> str:
        if isinstance(it, TextRun):
            return self.text_run(it)
        if isinstance(it, LineBreak):
            self.counts.text_chars += 1
            return "<br>"
        if isinstance(it, Hyperlink):
            inner = self.inlines(it.inlines, ctx_dir)
            if it.href:
                return f'<a href="{_a(it.href)}" rel="noopener noreferrer">{inner}</a>'
            return f'<span class="pc-link">{inner}</span>'
        if isinstance(it, DirSpan):
            inner = self.inlines(it.inlines, it.direction)
            tag = "bdo" if it.override else "span"
            return f'<{tag} dir="{it.direction}">{inner}</{tag}>'
        if isinstance(it, Image):
            return self.image(it.asset_id, it.width_px, it.height_px, it.alt,
                              "pc-image pc-floating" if it.floating else "pc-image")
        if isinstance(it, Equation):
            self.counts.text_chars += it.text_chars
            if it.mathml is not None:
                self.counts.mathml += 1
                return it.mathml
            a = self.registry.get(it.fallback_asset_id) if it.fallback_asset_id else None
            img = self.image(it.fallback_asset_id or 0, a.width_px if a else None, a.height_px if a else None,
                             it.alt, "pc-equation", style="vertical-align:middle")
            if it.display:
                return f'<span class="pc-equation-block" style="display:block;text-align:center">{img}</span>'
            return img
        if isinstance(it, TextBox):
            st = ["padding:4pt", "margin-top:4pt", "margin-bottom:4pt"]
            if it.bordered:
                st.insert(0, "border:1px solid #000000")
            if it.width_px:
                st.append(f"width:{_num(it.width_px)}px")
            body = "".join(self.block(b, ctx_dir) for b in it.blocks) or "<br>"
            return f'<div class="pc-textbox" style="{_a(";".join(st))}">{body}</div>'
        if isinstance(it, NoteReference):
            return f'<sup class="pc-noteref">{_t(it.mark)}</sup>'
        raise TypeError(f"unknown inline {type(it).__name__}")

    def image(self, asset_id: int, w, h, alt: str, cls: str, style: str | None = None) -> str:
        asset = self.registry.assets.get(asset_id)
        if asset is None or asset.path is None or asset.mime is None:
            self.counts.missing_assets.append(asset_id)
            return ""
        self.counts.images += 1
        w = w or asset.width_px
        h = h or asset.height_px
        attrs = f'class="{cls}" src="data:{asset.mime};base64,@@A{asset_id}@@" alt="{_a(alt)}"'
        if w:
            attrs += f' width="{max(1, round(w))}"'
        if h:
            attrs += f' height="{max(1, round(h))}"'
        if style:
            attrs += f' style="{_a(style)}"'
        return f"<img {attrs}>"

    def text_run(self, r: TextRun) -> str:
        self.counts.text_chars += len(r.text)
        parts = r.text.split("\t")
        html = TAB.join(_t(p) for p in parts)
        return self._wrap(html, r.fmt)

    @staticmethod
    def _wrap(html: str, f: RunFormat) -> str:
        styles = []
        if f.font_family:
            styles.append(f"font-family:'{f.font_family.replace(chr(39), '')}'")
        if f.font_size_pt:
            styles.append(f"font-size:{_num(f.font_size_pt)}pt")
        if f.color:
            styles.append(f"color:{f.color}")
        if f.highlight:
            styles.append(f"background-color:{f.highlight}")
        if f.caps:
            styles.append("text-transform:uppercase")
        if f.small_caps:
            styles.append("font-variant:small-caps")
        if f.underline and f.underline != "solid":
            styles.append(f"text-decoration:underline {f.underline}")
        if styles:
            html = f'<span style="{_a(";".join(styles))}">{html}</span>'
        if f.underline == "solid":
            html = f"<u>{html}</u>"
        if f.strike:
            html = f"<s>{html}</s>"
        if f.italic:
            html = f"<em>{html}</em>"
        if f.bold:
            html = f"<strong>{html}</strong>"
        if f.vertical_align == "superscript":
            html = f"<sup>{html}</sup>"
        elif f.vertical_align == "subscript":
            html = f"<sub>{html}</sub>"
        return html
