"""Render fragments of the original WordprocessingML to PNG via LibreOffice.

Each snippet becomes a minimal DOCX:
* ``exact`` mode: page size == the object's extent, zero margins, 1pt font, the
  object placed inline at the page origin; the rasterized page is cropped to the
  extent, so size and aspect ratio match the source exactly.
* ``crop`` mode (equations): a large page; the result is cropped to the inked area.

All snippets of a document are converted by one LibreOffice process per batch
(``--convert-to pdf``), then rasterized page 1 by ``pdftocairo`` at the
configured DPI.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree
from PIL import Image, ImageOps

from ..ooxml.namespaces import A, R, V, W, WP, qn
from ..ooxml.package_reader import DocxPackage
from .subprocess_runner import ConverterError, ConverterTimeout, run_isolated

EMU_PER_TWIP = 635
NS_DECL = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14"'
)
_CT_DEFAULTS = [
    ("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
    ("png", "image/png"), ("jpeg", "image/jpeg"), ("jpg", "image/jpeg"), ("gif", "image/gif"),
    ("tif", "image/tiff"), ("tiff", "image/tiff"), ("emf", "image/x-emf"), ("wmf", "image/x-wmf"),
    ("svg", "image/svg+xml"), ("bmp", "image/bmp"), ("bin", "application/vnd.openxmlformats-officedocument.oleObject"),
]
CROP_PAGE = (31680, 16000)  # twips (~56 x 28 cm) for equations
CALIBRATION_KEY = "zz_calibration"
CALIBRATION_EXTENT = (914400, 457200)  # 1in x 0.5in
RT_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _calibration_job(run_xml: bytes) -> "SnippetJob":
    buf = io.BytesIO()
    Image.new("RGB", (96, 48), "black").save(buf, "PNG")
    return SnippetJob(CALIBRATION_KEY, run_xml, None, {}, CALIBRATION_EXTENT, "exact",
                      blobs={"rIdM": ("calibration.png", buf.getvalue(), RT_IMAGE)})


@dataclass
class SnippetJob:
    key: str
    run_xml: bytes | None
    paragraph_xml: bytes | None
    parts: dict[str, tuple[str, str]]
    extent_emu: tuple[int, int] | None
    mode: str
    blobs: dict[str, tuple[str, bytes, str]] | None = None  # rId -> (name, bytes, rel type) not from the source package


def _to_inline(fragment: etree._Element) -> etree._Element:
    """wp:anchor -> wp:inline (keeps extent, docPr and graphic) so the object sits at the origin."""
    for anchor in list(fragment.iter(qn(WP, "anchor"))):
        inline = etree.Element(qn(WP, "inline"), nsmap=anchor.nsmap)
        for name in ("extent", "effectExtent", "docPr", "cNvGraphicFramePr"):
            el = anchor.find(qn(WP, name))
            if el is not None:
                inline.append(el)
        g = anchor.find(qn(A, "graphic"))
        if g is not None:
            inline.append(g)
        anchor.getparent().replace(anchor, inline)
    for el in fragment.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{" + V) and el.get("style"):
            parts = [p for p in el.get("style").split(";") if p.strip() and not p.strip().lower().startswith(
                ("position", "margin-left", "margin-top", "mso-position", "z-index", "left", "top"))]
            if el.getparent() is not None and el.getparent().tag == qn(W, "pict"):
                el.set("style", ";".join(parts))
    for blip in fragment.iter(qn(A, "blip")):
        if blip.get(qn(R, "link")):
            del blip.attrib[qn(R, "link")]
    return fragment


def build_snippet_docx(job: SnippetJob, source: DocxPackage, out: Path, part_limit: int) -> None:
    if job.mode == "exact" and job.extent_emu:
        # Slack around the object: LibreOffice offsets inline objects slightly (measured by calibration).
        w = max(20, -(-job.extent_emu[0] // EMU_PER_TWIP)) + 1440
        h = max(20, -(-job.extent_emu[1] // EMU_PER_TWIP)) + 720
    else:
        w, h = CROP_PAGE
    body = ""
    if job.run_xml is not None:
        frag = _to_inline(etree.fromstring(job.run_xml))
        body = ('<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'
                '<w:rPr><w:sz w:val="2"/></w:rPr></w:pPr><w:r><w:rPr><w:sz w:val="2"/></w:rPr>'
                + etree.tostring(frag, encoding="unicode") + "</w:r></w:p>")
        if frag.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
            body = '<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr>' + etree.tostring(frag, encoding="unicode") + "</w:p>"
    if job.paragraph_xml is not None:
        body += '<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr>' + job.paragraph_xml.decode("utf-8") + "</w:p>"
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS_DECL}><w:body>{body}'
           f'<w:sectPr><w:pgSz w:w="{w}" w:h="{h}"/><w:pgMar w:top="0" w:right="0" w:bottom="0" w:left="0" '
           f'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr></w:body></w:document>')
    rels = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    + "".join(f'<Default Extension="{e}" ContentType="{c}"/>' for e, c in _CT_DEFAULTS)
                    + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rIdDoc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", doc)
        for i, (rid, (part, rtype)) in enumerate(sorted(job.parts.items())):
            ext = part.rsplit(".", 1)[-1].lower() if "." in part else "bin"
            name = f"media/p{i}.{ext}"
            with source.open_part_seekable(part, part_limit) as src, zf.open("word/" + name, "w") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            rels.append(f'<Relationship Id="{escape(rid)}" Type="{escape(rtype)}" Target="{name}"/>')
        for rid, (name, data, rtype) in (job.blobs or {}).items():
            zf.writestr("word/media/" + name, data)
            rels.append(f'<Relationship Id="{escape(rid)}" Type="{escape(rtype)}" Target="media/{name}"/>')
        zf.writestr("word/_rels/document.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(rels) + "</Relationships>")


class SnippetRenderer:
    def __init__(self, workdir: Path, soffice: str, pdftocairo: str, dpi: int, timeout: int, batch_size: int):
        self.workdir = workdir
        self.soffice = soffice
        self.pdftocairo = pdftocairo
        self.dpi = dpi
        self.timeout = timeout
        self.batch_size = batch_size
        (workdir / "in").mkdir(parents=True, exist_ok=True)
        (workdir / "pdf").mkdir(exist_ok=True)
        (workdir / "png").mkdir(exist_ok=True)

    def render(self, jobs: list[SnippetJob], source: DocxPackage, part_limit: int, deadline_check) -> dict[str, Path | str]:
        """Return key -> PNG path, or key -> error string."""
        results: dict[str, Path | str] = {}
        inputs: dict[str, Path] = {}
        if any(j.mode == "exact" for j in jobs):
            from ..images.asset_pipeline import _pic_run_xml
            jobs = [*jobs, _calibration_job(_pic_run_xml("rIdM", *CALIBRATION_EXTENT))]
        self.origin: tuple[int, int] | None = None
        for job in jobs:
            deadline_check()
            path = self.workdir / "in" / f"{job.key}.docx"
            try:
                build_snippet_docx(job, source, path, part_limit)
                inputs[job.key] = path
            except Exception as exc:  # noqa: BLE001
                results[job.key] = f"SNIPPET_BUILD_FAILED:{type(exc).__name__}"
        keys = list(inputs)
        for start in range(0, len(keys), self.batch_size):
            deadline_check()
            batch = keys[start:start + self.batch_size]
            profile = (self.workdir / f"profile{start}").resolve()
            cmd = [self.soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
                   f"-env:UserInstallation=file://{profile}", "--convert-to", "pdf",
                   "--outdir", str(self.workdir / "pdf"), *[str(inputs[k]) for k in batch]]
            try:
                run_isolated(cmd, self.workdir, self.timeout)
            except ConverterTimeout:
                for k in batch:
                    results[k] = "LIBREOFFICE_TIMEOUT"
                continue
            shutil.rmtree(profile, ignore_errors=True)
        jobs_by_key = {j.key: j for j in jobs}
        if CALIBRATION_KEY in keys:
            keys.remove(CALIBRATION_KEY)
            keys.insert(0, CALIBRATION_KEY)
        for key in keys:
            if key in results:
                continue
            deadline_check()
            pdf = self.workdir / "pdf" / f"{key}.pdf"
            if not pdf.exists():
                results[key] = "LIBREOFFICE_CONVERSION_FAILED"
                continue
            base = self.workdir / "png" / key
            try:
                proc = run_isolated([self.pdftocairo, "-png", "-r", str(self.dpi), "-f", "1", "-l", "1", "-singlefile",
                                     str(pdf), str(base)], self.workdir, 120)
            except ConverterError:
                results[key] = "RASTERIZE_TIMEOUT"
                continue
            png = base.with_suffix(".png")
            if proc.returncode != 0 or not png.exists():
                results[key] = "RASTERIZE_FAILED"
                continue
            if key == CALIBRATION_KEY:
                self.origin = self._calibrate(png)
                continue
            try:
                results[key] = self._crop(png, jobs_by_key[key])
            except ConverterError as exc:
                results[key] = str(exc)
        results.pop(CALIBRATION_KEY, None)
        return results

    def _calibrate(self, png: Path) -> tuple[int, int] | None:
        """Locate the page position where LibreOffice places an inline object of known size."""
        with Image.open(png) as im:
            ink = ImageOps.invert(im.convert("L")).getbbox()
        if ink is None:
            return None
        ew = round(CALIBRATION_EXTENT[0] / 914400 * self.dpi)
        eh = round(CALIBRATION_EXTENT[1] / 914400 * self.dpi)
        if abs((ink[2] - ink[0]) - ew) > 2 or abs((ink[3] - ink[1]) - eh) > 2:
            return None
        return ink[0], ink[1]

    def _crop(self, png: Path, job: SnippetJob) -> Path:
        with Image.open(png) as im:
            im.load()
            rgb = im.convert("RGB")
        ink = ImageOps.invert(rgb.convert("L")).getbbox()
        if ink is None:
            raise ConverterError("EMPTY_RENDER")
        if job.mode == "exact" and job.extent_emu:
            if self.origin is None:
                raise ConverterError("CALIBRATION_FAILED")
            x0, y0 = self.origin
            w = max(1, round(job.extent_emu[0] / 914400 * self.dpi))
            h = max(1, round(job.extent_emu[1] / 914400 * self.dpi))
            box = (x0, y0, min(x0 + w, rgb.width), min(y0 + h, rgb.height))
            if ink[0] < x0 - 2 or ink[1] < y0 - 2 or ink[2] > box[2] + 2 or ink[3] > box[3] + 2:
                # Content outside the object's extent (e.g. shapes overflowing a group): never clip it.
                box = (min(ink[0], x0), min(ink[1], y0), max(ink[2], box[2]), max(ink[3], box[3]))
        else:
            pad = max(2, self.dpi // 48)
            box = (max(0, ink[0] - pad), max(0, ink[1] - pad), min(rgb.width, ink[2] + pad), min(rgb.height, ink[3] + pad))
        out = png.with_name(png.stem + ".final.png")
        buf = io.BytesIO()
        rgb.crop(box).save(buf, "PNG", optimize=True)
        out.write_bytes(buf.getvalue())
        return out
