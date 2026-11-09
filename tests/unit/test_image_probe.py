import io

import pytest
from PIL import Image

from patent_converter.analyzer.image_probe import probe_image, sniff_format
from tests.fixtures.docx_builder import SVG_SAMPLE, emf_bytes, png_bytes, raster_bytes, wmf_bytes

LIMITS = dict(max_pixels=50_000_000, max_dimension=20000, max_svg_bytes=1_000_000)


@pytest.mark.parametrize("data,fmt,size,conv", [
    (png_bytes(120, 80), "png", (120, 80), False),
    (raster_bytes("JPEG"), "jpeg", (100, 60), False),
    (raster_bytes("GIF"), "gif", (100, 60), False),
    (raster_bytes("TIFF"), "tiff", (100, 60), True),
    (emf_bytes(200, 100), "emf", None, True),
    (wmf_bytes(), "wmf", None, True),
    (SVG_SAMPLE, "svg", (120, 80), False),
])
def test_formats(data, fmt, size, conv):
    info = probe_image(f"word/media/x.{fmt}", io.BytesIO(data), len(data), **LIMITS)
    assert info.detected_format == fmt
    assert info.requires_conversion is conv
    assert not [i for i in info.issues if i != "EXTENSION_FORMAT_MISMATCH"]
    if size:
        assert (info.width_px, info.height_px) == size
    else:
        assert info.width_px and info.height_px


def test_alpha_detected():
    info = probe_image("a.png", io.BytesIO(png_bytes(10, 10, alpha=True)), 1, **LIMITS)
    assert info.has_alpha is True


def test_extension_mismatch():
    data = raster_bytes("JPEG")
    assert "EXTENSION_FORMAT_MISMATCH" in probe_image("a.png", io.BytesIO(data), len(data), **LIMITS).issues


def test_pixel_limit_checked_without_decoding():
    buf = io.BytesIO()
    Image.new("1", (12000, 12000)).save(buf, "PNG")  # small file, 144 MP
    data = buf.getvalue()
    info = probe_image("big.png", io.BytesIO(data), len(data), max_pixels=10_000_000, max_dimension=50000,
                       max_svg_bytes=1)
    assert "IMAGE_PIXEL_LIMIT_EXCEEDED" in info.issues


def test_svg_active_content_flagged():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><script>alert(1)</script>' \
          b'<image href="http://evil.example/x.png"/></svg>'
    info = probe_image("a.svg", io.BytesIO(svg), len(svg), **LIMITS)
    assert "SVG_ACTIVE_CONTENT" in info.issues and "SVG_EXTERNAL_REFERENCE" in info.issues


def test_svg_entity_attack_rejected():
    svg = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'
    info = probe_image("a.svg", io.BytesIO(svg), len(svg), **LIMITS)
    assert "SVG_UNSAFE_XML" in info.issues


def test_unknown_bytes():
    assert sniff_format(b"\x00\x01garbage") == "unknown"
