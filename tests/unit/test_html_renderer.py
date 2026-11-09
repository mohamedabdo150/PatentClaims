from pathlib import Path

from patent_converter.html.sanitizer import sanitize
from patent_converter.images.assets import AssetRegistry
from patent_converter.models.document_model import (
    CellBorder, Claim, Equation, Hyperlink, Image, LineBreak, NoteReference, Paragraph, ParagraphFormat, RunFormat,
    Table, TableCell, TableRow, TextBox, TextRun,
)
from patent_converter.renderers.html_renderer import HtmlRenderer
from patent_converter.renderers.html_writer import write_html


def registry(tmp_path):
    reg = AssetRegistry()
    aid = reg.media("word/media/a.png", None, (952500, 476250))
    a = reg.get(aid)
    a.path = tmp_path / "a.png"
    a.path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    a.mime = "image/png"
    return reg, aid


def test_render_formats_and_counts(tmp_path):
    reg, aid = registry(tmp_path)
    claim = Claim("1", "١", "ar")
    claim.blocks = [
        Paragraph([TextRun("مركب ", RunFormat()), TextRun("C", RunFormat(bold=True)),
                   TextRun("6", RunFormat(bold=True, vertical_align="subscript")), TextRun("\tx<y & z", RunFormat()),
                   LineBreak(), Hyperlink([TextRun("link", RunFormat())], "https://e.org"),
                   Image(aid, 100, 50, alt='a"b'), NoteReference("1", "footnote"),
                   Equation(False, '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>', text_chars=1)],
                  ParagraphFormat(direction="rtl", alignment="justify", numbering_label="١-")),
        Paragraph([TextRun("English", RunFormat(italic=True, underline="double", color="#FF0000"))],
                  ParagraphFormat(direction="ltr", margin_start_pt=18)),
        Table([TableRow([TableCell([Paragraph([TextRun("c")])], col_span=2, row_span=2,
                                   borders={"top": CellBorder("solid", 1, "#000000"), "start": None},
                                   background="#EEEEEE")])], direction="rtl"),
        Paragraph([TextBox([Paragraph([TextRun("box")])], False, 100, True)]),
        Paragraph([]),
    ]
    html, counts = HtmlRenderer(reg).render_claim(claim)
    assert html.startswith('<div class="pc-claim" dir="rtl" lang="ar">')
    assert '<span class="pc-label">١-</span>' in html
    assert "<strong>C</strong><strong><sub>6</sub></strong>" in html or "<sub><strong>6</strong></sub>" in html
    assert "x&lt;y &amp; z" in html and '<span style="white-space:pre">\t</span>' in html
    assert '<p dir="ltr" style="margin-left:18pt">' in html
    assert 'text-decoration:underline double' in html and "<em>" in html
    assert 'colspan="2" rowspan="2"' in html and "border-right:none" in html and "background-color:#EEEEEE" in html
    assert '<div class="pc-textbox"' in html and html.count("<div") == 3
    assert '<p dir="ltr"><br></p>' in html  # direction follows the source paragraph
    assert counts.text_chars == len("مركب C6\tx<y & z") + 1 + len("link") + 1 + len("English") + len("c") + len("box")
    assert counts.images == 1 and counts.mathml == 1 and counts.tables == 1
    clean, report = sanitize(html)
    assert report.clean and clean == html
    out = tmp_path / "c.html"
    write_html(html, reg, out)
    final = out.read_text(encoding="utf-8")
    assert "@@A" not in final and "data:image/png;base64,iVBORw0KGg" in final


def test_missing_asset_reported(tmp_path):
    reg, aid = registry(tmp_path)
    reg.get(aid).path = None
    html, counts = HtmlRenderer(reg).render_claim(Claim("1", "1", "en", [Paragraph([Image(aid, 1, 1)])]))
    assert counts.missing_assets == [aid] and "<img" not in html
    assert isinstance(Path, type)
