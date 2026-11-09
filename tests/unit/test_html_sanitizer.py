import pytest

from patent_converter.html.sanitizer import sanitize
from patent_converter.images.svg_sanitizer import SvgRejected, sanitize_svg

GOOD = ('<div class="pc-claim" dir="rtl" lang="ar"><p style="text-align:justify;margin-right:18pt">نص &amp; '
        '<strong>C<sub>6</sub>H<sub>6</sub></strong> <span style="font-family:&#x27;Traditional Arabic&#x27;">x</span>'
        '<img class="pc-image" src="data:image/png;base64,@@A3@@" alt="fig &quot;1&quot;" width="10" height="5">'
        '<a href="https://example.org/a?b=1&amp;c=2" rel="noopener noreferrer">l</a><br></p>'
        '<table class="pc-table" style="border-collapse:collapse"><tbody><tr><td colspan="2" rowspan="3" '
        'style="border-top:1px solid #000000">c</td></tr></tbody></table>'
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mfrac><mi>a</mi><mn>2</mn></mfrac>'
        '<mmultiscripts><mi>x</mi><mprescripts/><mn>1</mn><mn>2</mn></mmultiscripts>'
        '<mo fence="true" stretchy="true">(</mo></math></div>')


def test_approved_output_is_unchanged():
    out, report = sanitize(GOOD)
    assert report.clean, report
    assert out == GOOD


@pytest.mark.parametrize("bad,removed", [
    ('<p>a<script>alert(1)</script>b</p>', "script"),
    ('<img src="x" onerror="alert(1)">', "img@src"),
    ('<a href="javascript:alert(1)">x</a>', "a@href"),
    ('<p style="background:url(http://evil/x.png)">x</p>', "p@style"),
    ('<p style="width:expression(alert(1))">x</p>', "p@style"),
    ('<iframe src="http://evil"></iframe>', "iframe"),
    ('<img src="http://evil/track.png">', "img@src"),
    ('<p class="evil">x</p>', "p@class"),
    ('<svg><script>1</script></svg>', "svg"),
    ('<p><!-- c -->x</p>', "#comment"),
    ('<math xmlns="http://evil"><mi>x</mi></math>', "math@xmlns"),
])
def test_dangerous_content_removed(bad, removed):
    out, report = sanitize(bad)
    assert removed in report.removed_elements + report.removed_attributes
    assert "alert" not in out and "evil" not in out


def test_unknown_tags_unwrapped_text_kept():
    out, report = sanitize("<p><blink>keep me</blink></p>")
    assert out == "<p>keep me</p>" and report.removed_elements == ["blink"]


def test_svg_sanitizer():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="10" '
           b'height="10" onload="alert(1)"><script>alert(1)</script><foreignObject><p>x</p></foreignObject>'
           b'<rect width="5" height="5" style="fill:url(http://evil/a)"/><use xlink:href="#a"/>'
           b'<image xlink:href="http://evil/x.png"/><path d="M0 0L5 5"/></svg>')
    clean, removed = sanitize_svg(svg)
    assert b"script" not in clean and b"foreignObject" not in clean and b"onload" not in clean
    assert b"evil" not in clean and b'href="#a"' in clean and b"<path" in clean
    assert removed >= 5
    with pytest.raises(SvgRejected):
        sanitize_svg(b'<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>')
    with pytest.raises(SvgRejected):
        sanitize_svg(b"<html/>")
