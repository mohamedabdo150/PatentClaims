"""Allowlist SVG sanitizer.

Removes scripts, event handlers, foreignObject, animation, external references
and DTDs. Geometry, text and styling are kept so drawings render unchanged.
"""

from __future__ import annotations

import re

from lxml import etree

from ..ooxml.xml_parser import UnsafeXmlError, parse_bytes

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_ALLOWED = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "textPath",
    "defs", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "marker", "symbol", "use",
    "title", "desc", "image", "style", "filter", "feGaussianBlur", "feOffset", "feBlend", "feColorMatrix",
    "feFlood", "feComposite", "feMerge", "feMergeNode", "metadata",
}
_DANGEROUS_VALUE = re.compile(r"javascript:|vbscript:|expression\s*\(|@import|url\s*\(\s*['\"]?(?!#)", re.I)
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+$")


class SvgRejected(Exception):
    pass


def sanitize_svg(data: bytes) -> tuple[bytes, int]:
    """Return (sanitized SVG bytes, number of removed items)."""
    try:
        root = parse_bytes(data)
    except (UnsafeXmlError, etree.XMLSyntaxError) as exc:
        raise SvgRejected(str(exc)[:200]) from None
    if etree.QName(root).localname != "svg":
        raise SvgRejected("root element is not <svg>")
    removed = 0
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            if el.getparent() is not None:
                el.getparent().remove(el)
                removed += 1
            continue
        q = etree.QName(el)
        if q.namespace not in (SVG_NS, None) or q.localname not in _ALLOWED:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
                removed += 1
            continue
        for name, value in list(el.attrib.items()):
            aq = etree.QName(name)
            local = aq.localname.lower()
            if local.startswith("on") or _DANGEROUS_VALUE.search(value or ""):
                del el.attrib[name]
                removed += 1
            elif local == "href":
                if not (value.startswith("#") or (q.localname == "image" and _DATA_IMAGE.match(value))):
                    del el.attrib[name]
                    removed += 1
        if q.localname == "style" and el.text and _DANGEROUS_VALUE.search(el.text):
            el.text = _DANGEROUS_VALUE.sub("", el.text)
            removed += 1
    return etree.tostring(root, xml_declaration=False, encoding="utf-8"), removed
