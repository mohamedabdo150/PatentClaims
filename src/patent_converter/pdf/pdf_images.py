"""POST /pdf-to-images: embedded images of a PDF as Base64 (same output as the previous service).

Like the previous implementation, every image reference of every page is returned in
page order (an image used on two pages appears twice).
"""

from __future__ import annotations

import base64

from ..config.settings import PdfSettings
from .common import open_pdf


def extract_images(data: bytes, cfg: PdfSettings, deadline_check=lambda: None) -> list[str]:
    doc = open_pdf(data, cfg)
    images: list[str] = []
    try:
        for page in doc:
            deadline_check()
            for img in page.get_images(full=True):
                raw = doc.extract_image(img[0])["image"]
                images.append(base64.b64encode(raw).decode("ascii"))
    finally:
        doc.close()
    return images
