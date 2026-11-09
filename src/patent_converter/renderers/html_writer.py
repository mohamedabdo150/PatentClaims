"""Stream a sanitized HTML skeleton to disk, expanding asset placeholders to Base64.

Assets are read in chunks that are multiples of 3 bytes, so the Base64 output of
each chunk can be concatenated; memory use is bounded by the chunk size no
matter how large an image is.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from ..images.assets import AssetRegistry

_PLACEHOLDER = re.compile(r"@@A(\d+)@@")
CHUNK = 3 * 256 * 1024


def write_html(skeleton: str, registry: AssetRegistry, out: Path) -> int:
    """Write the final claim HTML; returns bytes written."""
    with out.open("w", encoding="utf-8", newline="") as fh:
        pos = 0
        for m in _PLACEHOLDER.finditer(skeleton):
            fh.write(skeleton[pos:m.start()])
            asset = registry.get(int(m.group(1)))
            assert asset.path is not None
            with asset.path.open("rb") as src:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(base64.b64encode(chunk).decode("ascii"))
            pos = m.end()
        fh.write(skeleton[pos:])
    return out.stat().st_size
