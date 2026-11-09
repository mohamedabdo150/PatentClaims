"""Header-only inspection of embedded media.

Images are never fully decoded here: Pillow's lazy ``Image.open`` reads only
headers, so dimension checks happen before any decompression.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import asdict, dataclass, field
from typing import IO

from lxml import etree
from PIL import Image

from ..ooxml.xml_parser import UnsafeXmlError, parse_bytes

BROWSER_NATIVE = {"png", "jpeg", "gif", "svg", "webp", "bmp"}

# Pillow's bomb warning is replaced by the explicit, configured pixel check below.
# Process-global and set once (never toggled per call) so concurrent probes cannot race.
warnings.simplefilter("ignore", Image.DecompressionBombWarning)


def configure_pillow_limit(max_pixels: int) -> None:
    """Pillow refuses to open images above 2 x MAX_IMAGE_PIXELS; align it with configuration once."""
    Image.MAX_IMAGE_PIXELS = max_pixels


@dataclass
class ImageInfo:
    part_name: str
    size_bytes: int
    detected_format: str
    extension: str
    width_px: int | None = None
    height_px: int | None = None
    mode: str | None = None
    frames: int | None = None
    dpi: tuple[float, float] | None = None
    has_alpha: bool | None = None
    browser_native: bool = False
    requires_conversion: bool = False
    issues: list[str] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return (self.width_px or 0) * (self.height_px or 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pixels"] = self.pixels
        return d


def sniff_format(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:4] == b"II\xbc\x01" or head[:3] == b"II\xbc":
        return "jxr"
    if head.startswith(b"BM"):
        return "bmp"
    if len(head) >= 44 and head[:4] == b"\x01\x00\x00\x00" and head[40:44] == b" EMF":
        return "emf"
    if head[:4] == b"\xd7\xcd\xc6\x9a" or head[:4] in (b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"):
        return "wmf"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole-cfb"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<") and b"<svg" in head.lower():
        return "svg"
    return "unknown"


_SVG_DANGER = re.compile(rb"<script|<foreignobject|\bon[a-z]+\s*=|javascript:", re.IGNORECASE)
_SVG_EXTERNAL_HREF = re.compile(rb"""(?:xlink:)?href\s*=\s*["'](?!#|data:)""", re.IGNORECASE)


def _probe_svg(data: bytes, info: ImageInfo) -> None:
    if _SVG_DANGER.search(data):
        info.issues.append("SVG_ACTIVE_CONTENT")
    if _SVG_EXTERNAL_HREF.search(data):
        info.issues.append("SVG_EXTERNAL_REFERENCE")
    try:
        root = parse_bytes(data)
    except UnsafeXmlError:
        info.issues.append("SVG_UNSAFE_XML")
        return
    except etree.XMLSyntaxError:
        info.issues.append("SVG_PARSE_ERROR")
        return

    def num(v: str | None) -> int | None:
        if not v:
            return None
        m = re.match(r"\s*([0-9.]+)", v)
        return int(float(m.group(1))) if m else None

    info.width_px, info.height_px = num(root.get("width")), num(root.get("height"))
    if (info.width_px is None or info.height_px is None) and root.get("viewBox"):
        parts = re.split(r"[\s,]+", root.get("viewBox", "").strip())
        if len(parts) == 4:
            info.width_px, info.height_px = num(parts[2]), num(parts[3])


def probe_image(
    part_name: str,
    stream: IO[bytes],
    size_bytes: int,
    max_pixels: int,
    max_dimension: int,
    max_svg_bytes: int,
) -> ImageInfo:
    head = stream.read(64)
    fmt = sniff_format(head)
    ext = part_name.rsplit(".", 1)[-1].lower() if "." in part_name else ""
    info = ImageInfo(part_name=part_name, size_bytes=size_bytes, detected_format=fmt, extension=ext)
    ext_alias = {"jpg": "jpeg", "jpe": "jpeg", "tif": "tiff", "wdp": "jxr", "hdp": "jxr"}.get(ext, ext)
    if fmt != "unknown" and ext_alias and ext_alias != fmt:
        info.issues.append("EXTENSION_FORMAT_MISMATCH")
    info.browser_native = fmt in BROWSER_NATIVE
    info.requires_conversion = fmt in {"tiff", "emf", "wmf", "jxr", "pdf", "unknown"}

    if fmt == "svg":
        if size_bytes > max_svg_bytes:
            info.issues.append("SVG_TOO_LARGE")
            return info
        _probe_svg(head + stream.read(max_svg_bytes), info)
        return info

    if fmt in {"unknown", "ole-cfb", "pdf", "jxr"}:
        info.issues.append("UNSUPPORTED_IMAGE_FORMAT")
        return info

    try:
        stream.seek(0)
    except Exception:  # noqa: BLE001 - non-seekable streams are re-read by caller
        info.issues.append("HEADER_PROBE_UNAVAILABLE")
        return info
    try:
        with Image.open(stream) as img:  # lazy: headers only
            info.width_px, info.height_px = img.size
            info.mode = img.mode
            info.has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
            dpi = img.info.get("dpi")
            if isinstance(dpi, (int, float)):
                info.dpi = (float(dpi), float(dpi))
            elif dpi:
                info.dpi = (round(float(dpi[0]), 2), round(float(dpi[1]), 2))
            info.frames = getattr(img, "n_frames", 1) if fmt in {"gif", "tiff"} else 1
    except Image.DecompressionBombError:
        info.issues.append("IMAGE_PIXEL_LIMIT_EXCEEDED")
    except Exception as exc:  # noqa: BLE001 - classify, never crash analysis
        info.issues.append(f"IMAGE_HEADER_UNREADABLE:{type(exc).__name__}")
    if info.width_px and info.height_px:
        if info.pixels > max_pixels and "IMAGE_PIXEL_LIMIT_EXCEEDED" not in info.issues:
            info.issues.append("IMAGE_PIXEL_LIMIT_EXCEEDED")
        if max(info.width_px, info.height_px) > max_dimension:
            info.issues.append("IMAGE_DIMENSION_LIMIT_EXCEEDED")
    if info.frames and info.frames > 1:
        info.issues.append("MULTI_FRAME_IMAGE")
    return info
