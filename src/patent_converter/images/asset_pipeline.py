"""Prepare every registered asset as a self-contained file ready for Base64 embedding.

Per asset (one at a time, buffers released after each):
* PNG / JPEG / GIF / WebP: format and pixel limits validated (header + Pillow verify), embedded as-is.
* BMP -> PNG; TIFF -> PNG (all frames stacked vertically, nothing dropped).
* SVG: sanitized (allowlist); the raster fallback is used if sanitizing fails.
* EMF / WMF and every snippet (groups, shapes, VML, equation fallbacks): rendered
  by the isolated LibreOffice snippet renderer.
Failures are recorded on the asset (``error``) and turned into conversion errors.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from PIL import Image

from ..analyzer.image_probe import sniff_format
from ..config.settings import MB, Settings
from ..conversion.snippets import SnippetJob, SnippetRenderer
from ..ooxml.namespaces import RT_IMAGE
from ..ooxml.package_reader import DocxPackage
from ..storage.temporary_storage import LO_PROFILES_DIR
from .assets import Asset, AssetRegistry
from .svg_sanitizer import SvgRejected, sanitize_svg

MIME = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}
EMU_PER_PX = 9525


def _pic_run_xml(rid: str, cx: int, cy: int) -> bytes:
    return (
        '<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<wp:inline><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="m"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        '<pic:nvPicPr><pic:cNvPr id="1" name="m"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
        "</wp:inline></w:drawing>"
    ).encode()


class AssetPipeline:
    def __init__(self, settings: Settings, workdir: Path, deadline_check):
        self.settings = settings
        self.workdir = workdir
        self.deadline_check = deadline_check
        self.limit = int(settings.conversion.max_asset_mb * MB)
        workdir.mkdir(parents=True, exist_ok=True)

    def prepare(self, registry: AssetRegistry, pkg: DocxPackage) -> dict[str, int]:
        stats = {"media": 0, "converted": 0, "snippets": 0, "svg": 0, "failed": 0}
        jobs: list[SnippetJob] = []
        for asset in registry:
            self.deadline_check()
            if asset.kind == "snippet":
                jobs.append(SnippetJob(f"a{asset.asset_id}", asset.snippet_xml, asset.snippet_paragraph,
                                       asset.snippet_parts, asset.extent_emu, asset.snippet_mode))
                stats["snippets"] += 1
                continue
            stats["media"] += 1
            job = self._media(asset, pkg, stats)
            if job is not None:
                jobs.append(job)
        if jobs:
            renderer = SnippetRenderer(
                self.workdir / "snippets", self.settings.conversion.soffice_path,
                self.settings.conversion.pdftocairo_path, self.settings.conversion.raster_dpi,
                self.settings.conversion.libreoffice_timeout_seconds, self.settings.conversion.libreoffice_batch_size,
                self.settings.conversion.libreoffice_parallelism,
                profile_root=self.settings.storage.temporary_directory / LO_PROFILES_DIR)
            results = renderer.render(jobs, pkg, self.limit, self.deadline_check)
            dpi = self.settings.conversion.raster_dpi
            for key, res in results.items():
                asset = registry.get(int(key[1:]))
                if isinstance(res, str):
                    asset.error = res
                    stats["failed"] += 1
                    continue
                asset.path, asset.mime = res, "image/png"
                with Image.open(res) as im:
                    asset.width_px = round(im.width * 96 / dpi)
                    asset.height_px = round(im.height * 96 / dpi)
        stats["failed"] = sum(1 for a in registry if a.error)
        return stats

    # ------------------------------------------------------------------ media
    def _media(self, asset: Asset, pkg: DocxPackage, stats: dict) -> SnippetJob | None:
        assert asset.part_name
        sec = self.settings.security
        if asset.svg_part and self.settings.conversion.prefer_svg and pkg.has_part(asset.svg_part):
            try:
                data = pkg.read_part(asset.svg_part, int(sec.max_svg_size_mb * MB))
                clean, _removed = sanitize_svg(data)
                out = self.workdir / f"a{asset.asset_id}.svg"
                out.write_bytes(clean)
                asset.path, asset.mime = out, MIME["svg"]
                stats["svg"] += 1
                return None
            except (SvgRejected, Exception):  # noqa: BLE001 - fall back to the raster blip
                pass
        if not pkg.has_part(asset.part_name):
            asset.error = "MISSING_MEDIA_PART"
            return None
        raw = self.workdir / f"a{asset.asset_id}.src"
        with pkg.open_part_seekable(asset.part_name, self.limit) as src, raw.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        with raw.open("rb") as fh:
            fmt = sniff_format(fh.read(64))
        if fmt in ("emf", "wmf"):
            extent = asset.extent_emu or self._native_extent(raw)
            stats["converted"] += 1
            return SnippetJob(f"a{asset.asset_id}", _pic_run_xml("rIdM", *extent), None,
                              {"rIdM": (asset.part_name, RT_IMAGE)}, extent, "exact")
        if fmt == "svg":
            try:
                clean, _ = sanitize_svg(raw.read_bytes())
            except SvgRejected as exc:
                asset.error = f"SVG_REJECTED:{exc}"[:120]
                return None
            out = raw.with_suffix(".svg")
            out.write_bytes(clean)
            asset.path, asset.mime = out, MIME["svg"]
            return None
        if fmt not in ("png", "jpeg", "gif", "webp", "bmp", "tiff"):
            asset.error = f"UNSUPPORTED_IMAGE_FORMAT:{fmt}"
            return None
        try:
            with Image.open(raw) as im:
                w, h = im.size
                if w * h > sec.max_image_pixels or max(w, h) > sec.max_image_dimension_px:
                    asset.error = "IMAGE_PIXEL_LIMIT_EXCEEDED"
                    return None
                if fmt in ("png", "jpeg", "gif", "webp"):
                    im.verify()  # structural check without full decode
                    asset.path, asset.mime = raw, MIME[fmt]
                else:
                    out = raw.with_suffix(".png")
                    self._to_png(im, out)
                    asset.path, asset.mime = out, MIME["png"]
                    stats["converted"] += 1
                asset.width_px, asset.height_px = w, h
        except Exception as exc:  # noqa: BLE001
            asset.error = f"IMAGE_INVALID:{type(exc).__name__}"
        return None

    @staticmethod
    def _to_png(im: Image.Image, out: Path) -> None:
        frames = []
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            fr = im.copy()
            frames.append(fr.convert("RGBA") if fr.mode in ("RGBA", "LA", "P", "PA") else fr.convert("RGB"))
        if len(frames) == 1:
            canvas = frames[0]
        else:
            width = max(f.width for f in frames)
            canvas = Image.new("RGB", (width, sum(f.height for f in frames)), "white")
            y = 0
            for f in frames:
                canvas.paste(f, (0, y))
                y += f.height
        buf = io.BytesIO()
        canvas.save(buf, "PNG", optimize=False)
        out.write_bytes(buf.getvalue())

    @staticmethod
    def _native_extent(path: Path) -> tuple[int, int]:
        try:
            with Image.open(path) as im:
                return im.width * EMU_PER_PX, im.height * EMU_PER_PX
        except Exception:  # noqa: BLE001
            return 96 * EMU_PER_PX, 96 * EMU_PER_PX
