from lxml import etree

from patent_converter.analyzer.block_scanner import paragraph_text, scan_block
from tests.fixtures.docx_builder import (
    NS_DECL, grouped_drawing, omath, omml_fraction, para, run, sym, textbox, tracked_delete, tracked_insert,
)


def el(xml: str):
    return etree.fromstring(f"<w:body {NS_DECL}>{xml}</w:body>")[0]


def test_alternate_content_counts_choice_only():
    p = el(para(run("x"), textbox("inside box", 5)))
    s = scan_block(p)
    assert s.counts["textboxes"] == 1  # VML fallback copy not counted
    assert s.counts["vml_pict"] == 0
    assert s.counts["alternate_content"] == 1
    assert s.counts["drawing_anchor"] == 1


def test_group_drawing_counts():
    s = scan_block(el(para(grouped_drawing("rId1", "rId2", "Fig", 10))))
    assert s.counts["drawing_groups"] == 1 and s.counts["pictures"] == 2 and s.counts["drawing_shapes"] == 1
    assert sorted(r.rel_id for r in s.references) == ["rId1", "rId2"]


def test_paragraph_text_excludes_boxes_deletions_and_instructions():
    p = el(para(run("1. Keep "), tracked_insert("ins "), tracked_delete("DROP "), textbox("BOX", 3),
                sym("Symbol", "F061"), "<w:r><w:instrText>SEQ X</w:instrText></w:r>", run(" end")))
    assert paragraph_text(p) == "1. Keep ins  end"
    s = scan_block(p)
    assert s.counts["tracked_insertions"] == 1 and s.counts["tracked_deletions"] == 1
    assert s.counts["symbols"] == 1 and s.symbol_fonts["Symbol"] == 1


def test_omml_and_unknown_elements():
    xml = para(run("a")).replace("</w:p>", omath(omml_fraction("1", "2")) + "<w:foo/></w:p>")
    s = scan_block(el(xml))
    assert s.counts["equations"] == 1 and s.omml_constructs["f"] == 1
    assert s.unknown_elements["w:foo"] == 1


def test_deep_nesting_does_not_overflow():
    # Built programmatically: the parser itself refuses depth > 256 without huge_tree.
    from patent_converter.ooxml.namespaces import W, qn
    p = etree.Element(qn(W, "p"))
    node = p
    for _ in range(3000):  # deeper than Python's default recursion limit
        node = etree.SubElement(node, qn(W, "smartTag"))
    etree.SubElement(node, qn(W, "r"))
    assert scan_block(p).counts["runs"] == 1
