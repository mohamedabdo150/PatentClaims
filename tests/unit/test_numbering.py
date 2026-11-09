from lxml import etree

from patent_converter.ooxml.namespaces import W, qn
from patent_converter.ooxml.numbering import NumberingResolver

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def resolver(levels: str, nums: str, styles: str = "") -> NumberingResolver:
    r = NumberingResolver()
    r.load_numbering(etree.fromstring(f'<w:numbering {NS}><w:abstractNum w:abstractNumId="0">{levels}'
                                      f"</w:abstractNum>{nums}</w:numbering>"))
    if styles:
        r.load_styles(etree.fromstring(f"<w:styles {NS}>{styles}</w:styles>"))
    return r


def lvl(i, fmt, text, start=1, extra=""):
    return (f'<w:lvl w:ilvl="{i}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
            f'<w:lvlText w:val="{text}"/>{extra}</w:lvl>')


NUM1 = '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'


def test_decimal_and_nested_levels():
    r = resolver(lvl(0, "decimal", "%1.") + lvl(1, "lowerLetter", "(%2)"), NUM1)
    assert [r.next_label("1", 0), r.next_label("1", 1), r.next_label("1", 1), r.next_label("1", 0),
            r.next_label("1", 1)] == ["1.\t", "(a)\t", "(b)\t", "2.\t", "(a)\t"]


def test_arabic_formats():
    r = resolver(lvl(0, "hindiNumbers", "%1-") + lvl(1, "arabicAlpha", "%2)") + lvl(2, "arabicAbjad", "%3."), NUM1)
    labels = [r.next_label("1", 0) for _ in range(12)]
    assert labels[0] == "١-\t" and labels[11] == "١٢-\t"
    assert r.next_label("1", 1) == "أ)\t" and r.next_label("1", 1) == "ب)\t"
    assert r.next_label("1", 2) == "أ.\t" and r.next_label("1", 2) == "ب.\t" and r.next_label("1", 2) == "ج.\t"


def test_start_override_restarts_list():
    nums = NUM1 + '<w:num w:numId="2"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
    r = resolver(lvl(0, "decimal", "%1."), nums)
    assert [r.next_label("1", 0), r.next_label("1", 0), r.next_label("2", 0), r.next_label("2", 0)] == \
        ["1.\t", "2.\t", "1.\t", "2.\t"]


def test_style_numbering_and_unsupported_format():
    styles = ('<w:style w:styleId="Claim"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
              '<w:style w:styleId="ClaimSub"><w:basedOn w:val="Claim"/></w:style>')
    r = resolver(lvl(0, "chineseCounting", "%1."), NUM1, styles)
    ppr = etree.fromstring(f'<w:pPr {NS}><w:pStyle w:val="ClaimSub"/></w:pPr>')
    assert r.paragraph_numpr(ppr) == ("1", 0)
    assert r.next_label("1", 0) == "1.\t"
    assert r.unsupported_formats["chineseCounting"] == 1
    off = etree.fromstring(f'<w:pPr {NS}><w:pStyle w:val="Claim"/><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr>')
    assert r.paragraph_numpr(off) is None
    assert qn(W, "p")  # namespace helper sanity
