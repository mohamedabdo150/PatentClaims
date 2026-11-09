"""Registry of visual assets referenced by claim models.

An asset is either a media part of the DOCX package (converted when needed) or
a *snippet*: a fragment of the original WordprocessingML (drawing group, VML
shape, equation) that is rendered by the isolated LibreOffice renderer.
Assets are prepared to files on disk; HTML only references them by id until
the final Base64 streaming step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class Asset:
    asset_id: int
    kind: Literal["media", "snippet"]
    part_name: str | None = None          # media: package part
    svg_part: str | None = None           # media: optional asvg:svgBlip part
    extent_emu: tuple[int, int] | None = None
    snippet_xml: bytes | None = None      # snippet: run-level XML to place inside <w:p><w:r>…
    snippet_paragraph: bytes | None = None  # snippet: paragraph-level XML (display equations)
    snippet_parts: dict[str, tuple[str, str]] = field(default_factory=dict)  # rId -> (part, rel type)
    snippet_mode: Literal["exact", "crop"] = "exact"
    purpose: str = "image"                # image | drawing | vml | equation | metafile
    # results
    path: Path | None = None
    mime: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    error: str | None = None


class AssetRegistry:
    def __init__(self) -> None:
        self.assets: dict[int, Asset] = {}
        self._media_keys: dict[tuple, int] = {}

    def _new(self, **kw) -> Asset:
        aid = len(self.assets) + 1
        a = Asset(asset_id=aid, **kw)
        self.assets[aid] = a
        return a

    def media(self, part_name: str, svg_part: str | None, extent: tuple[int, int] | None) -> int:
        key = (part_name, svg_part, extent)
        if key not in self._media_keys:
            self._media_keys[key] = self._new(kind="media", part_name=part_name, svg_part=svg_part,
                                              extent_emu=extent).asset_id
        return self._media_keys[key]

    def snippet(self, purpose: str, run_xml: bytes | None, parts: dict[str, tuple[str, str]],
                extent: tuple[int, int] | None, mode: str, paragraph_xml: bytes | None = None) -> int:
        return self._new(kind="snippet", purpose=purpose, snippet_xml=run_xml, snippet_paragraph=paragraph_xml,
                         snippet_parts=parts, extent_emu=extent, snippet_mode=mode).asset_id

    def __iter__(self):
        return iter(self.assets.values())

    def get(self, aid: int) -> Asset:
        return self.assets[aid]
