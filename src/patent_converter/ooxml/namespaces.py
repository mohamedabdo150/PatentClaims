"""OOXML namespace URIs and Clark-notation helpers."""

from __future__ import annotations

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPC = "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
WP14 = "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
W16SE = "http://schemas.microsoft.com/office/word/2015/wordml/symex"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
W10 = "urn:schemas-microsoft-com:office:word"
ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
A14 = "http://schemas.microsoft.com/office/drawing/2010/main"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"

# ISO/IEC 29500 Strict uses different namespaces; detected and reported.
STRICT_W = "http://purl.oclc.org/ooxml/wordprocessingml/main"

PREFIXES: dict[str, str] = {
    W: "w", R: "r", M: "m", WP: "wp", A: "a", PIC: "pic", C: "c", DGM: "dgm",
    WPS: "wps", WPG: "wpg", WPC: "wpc", WP14: "wp14", W14: "w14", W15: "w15",
    W16SE: "w16se", MC: "mc", V: "v", O: "o", W10: "w10", ASVG: "asvg", A14: "a14",
}

# Relationship types
RT_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
RT_OFFICE_DOCUMENT = RT_BASE + "officeDocument"
RT_OFFICE_DOCUMENT_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
RT_IMAGE = RT_BASE + "image"
RT_OLE = RT_BASE + "oleObject"
RT_PACKAGE = RT_BASE + "package"
RT_HYPERLINK = RT_BASE + "hyperlink"
RT_NUMBERING = RT_BASE + "numbering"
RT_STYLES = RT_BASE + "styles"
RT_HEADER = RT_BASE + "header"
RT_FOOTER = RT_BASE + "footer"
RT_FOOTNOTES = RT_BASE + "footnotes"
RT_ENDNOTES = RT_BASE + "endnotes"
RT_CHART = RT_BASE + "chart"
RT_AFCHUNK = RT_BASE + "aFChunk"
RT_VBA = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"

MACRO_CONTENT_TYPES = {
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
    "application/vnd.ms-office.vbaProject",
}


def qn(ns: str, local: str) -> str:
    """Clark notation ``{ns}local``."""
    return f"{{{ns}}}{local}"


def split_qname(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, local
    return "", tag


def prefixed(tag: str) -> str:
    """Human-readable ``prefix:local`` for reports."""
    ns, local = split_qname(tag)
    if not ns:
        return local
    return f"{PREFIXES.get(ns, ns)}:{local}"
