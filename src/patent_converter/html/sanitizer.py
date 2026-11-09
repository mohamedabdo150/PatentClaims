"""Allowlist HTML sanitizer aware of the converter's approved output.

Sanitizes the HTML *skeleton* (asset bytes are inserted later), so the input is
small. Approved: semantic text markup, tables, images with data: URIs,
hyperlinks with safe schemes, direction attributes, controlled ``pc-`` classes,
an allowlist of CSS properties, and MathML Core elements.

``sanitize()`` reports everything it removes. The pipeline requires the report
to be empty for its own output, so legitimate patent content can never be
silently stripped by the sanitizer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

HTML_ELEMENTS = {"div", "p", "span", "strong", "em", "u", "s", "sub", "sup", "br", "a", "img", "table", "tbody",
                 "tr", "td", "th", "bdo"}
MATH_ELEMENTS = {"math", "mrow", "mi", "mn", "mo", "mtext", "mfrac", "msup", "msub", "msubsup", "msqrt", "mroot",
                 "munder", "mover", "munderover", "mmultiscripts", "mprescripts", "mtable", "mtr", "mtd", "menclose",
                 "mphantom", "mspace", "mstyle", "none"}
VOID = {"br", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "form", "input",
                     "textarea", "select", "button", "link", "meta", "base", "frame", "frameset", "applet"}
GLOBAL_ATTRS = {"dir", "lang", "class", "style"}
ELEMENT_ATTRS = {
    "a": {"href", "rel", "title"}, "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
    "math": {"xmlns", "display"}, "mfrac": {"linethickness", "bevelled"},
    "mo": {"fence", "stretchy", "separator", "largeop", "movablelimits", "form"},
    "mi": {"mathvariant"}, "mover": {"accent"}, "munder": {"accentunder"}, "menclose": {"notation"},
    "mtable": {"columnalign"},
}
CSS_PROPS = {"text-align", "margin-left", "margin-right", "margin-top", "margin-bottom", "text-indent", "line-height",
             "font-family", "font-size", "color", "background-color", "border", "border-top", "border-right",
             "border-bottom", "border-left", "border-collapse", "vertical-align", "width", "white-space", "padding",
             "display", "text-decoration", "text-transform", "font-variant", "unicode-bidi"}
_CSS_VALUE = re.compile(r"^[\w\s#.,%'\"()+\-]*$", re.UNICODE)
_CSS_BAD = re.compile(r"url\s*\(|expression|javascript|behaviou?r|@|\\|/\*", re.I)
_DATA_SRC = re.compile(r"^data:image/(png|jpeg|gif|webp|svg\+xml);base64,(@@A\d+@@|[A-Za-z0-9+/=]+)$")
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.I)
_ENUM = {
    "dir": {"ltr", "rtl"}, "display": {"block", "inline"}, "mathvariant": {"normal", "bold", "italic", "bold-italic",
    "double-struck", "fraktur", "script", "sans-serif", "monospace"}, "fence": {"true", "false"},
    "stretchy": {"true", "false"}, "separator": {"true", "false"}, "largeop": {"true", "false"},
    "movablelimits": {"true", "false"}, "accent": {"true", "false"}, "accentunder": {"true", "false"},
    "bevelled": {"true", "false"}, "notation": {"box"}, "columnalign": {"left", "center", "right"},
    "form": {"prefix", "infix", "postfix"}, "linethickness": {"0"},
}


@dataclass
class SanitizeReport:
    removed_elements: list[str] = field(default_factory=list)
    removed_attributes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.removed_elements and not self.removed_attributes


def _clean_style(value: str) -> str | None:
    out = []
    for decl in value.split(";"):
        if not decl.strip():
            continue
        if ":" not in decl:
            return None
        prop, val = decl.split(":", 1)
        prop, val = prop.strip().lower(), val.strip()
        if prop not in CSS_PROPS or not _CSS_VALUE.match(val) or _CSS_BAD.search(val):
            return None
        out.append(f"{prop}:{val}")
    return ";".join(out)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.report = SanitizeReport()
        self.drop_depth = 0
        self.stack: list[str] = []

    def _attrs(self, tag: str, attrs) -> str:
        parts = []
        allowed = GLOBAL_ATTRS | ELEMENT_ATTRS.get(tag, set())
        for name, value in attrs:
            value = value if value is not None else ""
            ok = name in allowed
            if ok:
                if name == "style":
                    cleaned = _clean_style(value)
                    ok = cleaned is not None and cleaned == value
                elif name == "class":
                    ok = all(re.fullmatch(r"pc-[a-z0-9-]+", c) for c in value.split())
                elif name == "lang":
                    ok = bool(re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", value))
                elif name == "href":
                    ok = bool(_SAFE_HREF.match(value))
                elif name == "rel":
                    ok = value == "noopener noreferrer"
                elif name == "src":
                    ok = tag == "img" and bool(_DATA_SRC.match(value))
                elif name in ("width", "height", "colspan", "rowspan"):
                    ok = value.isdigit() and 0 < int(value) < 100000
                elif name == "xmlns":
                    ok = value == "http://www.w3.org/1998/Math/MathML"
                elif name in _ENUM:
                    ok = value in _ENUM[name]
            if ok:
                parts.append(f' {name}="{escape(value, quote=True)}"')
            else:
                self.report.removed_attributes.append(f"{tag}@{name}")
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, selfclosing=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, selfclosing=True)

    def _start(self, tag, attrs, selfclosing):
        if self.drop_depth:
            if tag not in VOID and not selfclosing:
                self.drop_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.report.removed_elements.append(tag)
            if not selfclosing and tag not in VOID:
                self.drop_depth = 1
            return
        if tag not in HTML_ELEMENTS and tag not in MATH_ELEMENTS:
            self.report.removed_elements.append(tag)  # unwrap: keep content
            return
        a = self._attrs(tag, attrs)
        if tag in VOID:
            self.out.append(f"<{tag}{a}>")
        elif selfclosing:
            self.out.append(f"<{tag}{a}/>")
        else:
            self.out.append(f"<{tag}{a}>")
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.drop_depth:
            self.drop_depth -= 1
            return
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(escape(data, quote=False))

    def handle_comment(self, data):
        self.report.removed_elements.append("#comment")

    def handle_pi(self, data):
        self.report.removed_elements.append("#pi")

    def handle_decl(self, decl):
        self.report.removed_elements.append("#decl")

    def unknown_decl(self, data):
        self.report.removed_elements.append("#decl")


def sanitize(html: str) -> tuple[str, SanitizeReport]:
    s = _Sanitizer()
    s.feed(html)
    s.close()
    while s.stack:
        s.out.append(f"</{s.stack.pop()}>")
    return "".join(s.out), s.report
