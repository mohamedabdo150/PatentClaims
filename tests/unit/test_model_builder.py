from lxml import etree

from patent_converter.extraction.model_builder import BuildContext, ContentPolicy, ModelBuilder
from patent_converter.images.assets import AssetRegistry
from patent_converter.models.document_model import (
    Claim, Equation, Hyperlink, Image, LineBreak, NoteReference, Paragraph, Table, TextBox, TextRun,
)
from patent_converter.ooxml.relationships import Relationship
from patent_converter.ooxml.styles import StyleResolver
from tests.fixtures.docx_builder import (
    NS_DECL, anchored_image, cell, grouped_drawing, hyperlink, inline_image, omath, omml_fraction, para, run, sym,
    table, textbox, tracked_insert,
)

RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def build(xml: str, policy: ContentPolicy | None = None, styles_xml: str | None = None):
    rels = {
        "rId1": Relationship("rId1", RT + "image", "media/a.png", False, "word/media/a.png"),
        "rId2": Relationship("rId2", RT + "image", "media/b.png", False, "word/media/b.png"),
        "rIdH": Relationship("rIdH", RT + "hyperlink", "https://example.org/x", True, None),
        "rIdJ": Relationship("rIdJ", RT + "hyperlink", "javascript:alert(1)", True, None),
    }
    styles = StyleResolver()
    if styles_xml:
        styles.load(etree.fromstring(styles_xml))
    ctx = BuildContext(rels=rels, styles=styles, registry=AssetRegistry(), language="en", policy=policy or ContentPolicy())
    ctx.claim = Claim("1", "1", "en")
    body = etree.fromstring(f"<w:body {NS_DECL}>{xml}</w:body>")
    blocks = ModelBuilder(ctx).blocks(list(body))
    return blocks, ctx


def test_runs_formatting_and_merging():
    blocks, ctx = build(para(run("C"), run("6", vert="subscript"), run("H"), run("6", vert="subscript"),
                             run(" bold", b=True), run(" bold2", b=True)))
    inl = blocks[0].inlines
    assert [i.text for i in inl] == ["C", "6", "H", "6", " bold bold2"]
    assert inl[1].fmt.vertical_align == "subscript" and inl[4].fmt.bold
    assert ctx.claim.counters.text_chars == len("C6H6 bold bold2")


def test_symbol_font_mapped_to_unicode():
    blocks, ctx = build(para(sym("Symbol", "F061"), run("b", font="Symbol"), run("-pinene")))
    assert "".join(i.text for i in blocks[0].inlines) == "αβ-pinene"
    assert ctx.claim.counters.symbol_chars_mapped == 2


def test_images_drawings_textboxes():
    blocks, ctx = build(para(inline_image("rId1", 100, 50, 1), anchored_image("rId2", 10, 10, 2), textbox("box", 3),
                             grouped_drawing("rId1", "rId2", "Fig", 4)))
    inl = blocks[0].inlines
    assert isinstance(inl[0], Image) and inl[0].width_px == 100 and not inl[0].floating
    assert isinstance(inl[1], Image) and inl[1].floating
    assert isinstance(inl[2], TextBox) and inl[2].blocks[0].inlines[0].text == "box"
    assert isinstance(inl[3], Image) and inl[3].source == "drawing"
    k = ctx.claim.counters
    assert k.image_refs == 4 and k.drawings_rasterized == 1 and k.text_chars_rasterized == 3
    kinds = sorted(a.kind for a in ctx.registry)
    assert kinds == ["media", "media", "snippet"]


def test_hyperlinks_scheme_filter():
    blocks, ctx = build(para(hyperlink("rIdH", "ok"), hyperlink("rIdJ", "bad")))
    links = [i for i in blocks[0].inlines if isinstance(i, Hyperlink)]
    assert links[0].href == "https://example.org/x" and links[1].href is None
    assert any(i.code == "HYPERLINK_SCHEME_DROPPED" for i in ctx.claim.issues)


def test_equations_and_tables_with_merges():
    t = table([[cell("A", vmerge="restart"), cell("B", span=2)],
               [cell("", vmerge="continue"), cell("C"), cell("D")]], cols=3)
    blocks, ctx = build(para(run("x"), omath(omml_fraction("1", "2"))) + t)
    eq = blocks[0].inlines[1]
    assert isinstance(eq, Equation) and eq.mathml and "<mfrac>" in eq.mathml and eq.text_chars == 2
    tbl = blocks[1]
    assert isinstance(tbl, Table)
    assert [(c.col_span, c.row_span) for c in tbl.rows[0].cells] == [(1, 2), (2, 1)]
    assert len(tbl.rows[1].cells) == 2


def test_tracked_changes_policy():
    _, ctx = build(para(run("a"), tracked_insert("b")))
    assert any(i.code == "TRACKED_CHANGES" and i.severity == "error" for i in ctx.claim.issues)
    _, ctx2 = build(para(run("a"), tracked_insert("b")), ContentPolicy(tracked_changes="accept"))
    assert not ctx2.claim.issues and ctx2.claim.counters.text_chars == 2


def test_breaks_and_notes():
    blocks, _ = build(para(run("a"), "<w:r><w:br/></w:r>", '<w:r><w:footnoteReference w:id="2"/></w:r>'))
    inl = blocks[0].inlines
    assert isinstance(inl[1], LineBreak) and isinstance(inl[2], NoteReference) and inl[2].mark == "1"


def test_style_inheritance():
    styles = ('<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman"/><w:sz w:val="24"/></w:rPr>'
              '</w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"/>'
              '<w:style w:type="character" w:styleId="Strong"><w:rPr><w:b/></w:rPr></w:style></w:styles>')
    xml = para('<w:r><w:rPr><w:rStyle w:val="Strong"/><w:sz w:val="28"/></w:rPr><w:t>x</w:t></w:r>', run("y"))
    blocks, _ = build(xml, styles_xml=styles)
    a, b = blocks[0].inlines
    assert a.fmt.bold and a.fmt.font_size_pt == 14.0 and a.fmt.font_family is None
    assert not b.fmt.bold and b.fmt.font_size_pt is None
    assert isinstance(blocks[0], Paragraph)
