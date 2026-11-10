"""Render fragments of the original WordprocessingML to PNG via LibreOffice.

Each snippet becomes a minimal DOCX:
* ``exact`` mode: page size == the object's extent, zero margins, 1pt font, the
  object placed inline at the page origin; the rasterized page is cropped to the
  extent, so size and aspect ratio match the source exactly.
* ``crop`` mode (equations): a large page; the result is cropped to the inked area.

Snippets are combined into batch documents (one page per snippet); each batch is
converted by one LibreOffice process and rasterized by one ``pdftocairo`` run.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree
from PIL import Image, ImageChops, ImageOps

from ..ooxml.namespaces import A, R, V, W, WP, qn
from ..ooxml.package_reader import DocxPackage
from ..ooxml.relationships import rels_part_for
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
_CONTEXT_PARTS = {"theme", "styles", "numbering", "fontTable"}
SLACK_TWIPS = (1440, 720)  # 1 in x 0.5 in (smaller slack shifts anti-aliasing, no real speed gain)
CROP_PAGE = (31680, 16000)  # twips (~56 x 28 cm) for equations
CALIBRATION_KEY = "zz_calibration"
CALIBRATION_EXTENT = (914400, 457200)  # 1in x 0.5in
RT_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _calibration_job(run_xml: bytes) -> "SnippetJob":
    buf = io.BytesIO()
    Image.new("RGB", (96, 48), "black").save(buf, "PNG")
    return SnippetJob(CALIBRATION_KEY, run_xml, None, {}, CALIBRATION_EXTENT, "exact",
                      blobs={"rIdCal": ("calibration.png", buf.getvalue(), RT_IMAGE)})


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


def _page_twips(job: SnippetJob) -> tuple[int, int]:
    if job.mode == "exact" and job.extent_emu:
        # Slack around the object: LibreOffice offsets inline objects slightly (0.125 in, measured
        # by the calibration page); generous slack keeps the object fully on the page.
        return (max(20, -(-job.extent_emu[0] // EMU_PER_TWIP)) + SLACK_TWIPS[0],
                max(20, -(-job.extent_emu[1] // EMU_PER_TWIP)) + SLACK_TWIPS[1])
    return CROP_PAGE


def _sect_pr(w: int, h: int) -> str:
    return (f'<w:sectPr><w:pgSz w:w="{w}" w:h="{h}"/><w:pgMar w:top="0" w:right="0" w:bottom="0" w:left="0" '
            f'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>')


def _job_paragraphs(job: SnippetJob) -> list[tuple[str, str]]:
    """(pPr children, paragraph content) of one snippet; the caller appends the section break."""
    paras: list[tuple[str, str]] = []
    if job.run_xml is not None:
        frag = _to_inline(etree.fromstring(job.run_xml))
        xml = etree.tostring(frag, encoding="unicode")
        if frag.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
            paras.append(('<w:spacing w:before="0" w:after="0"/>', xml))
        else:
            paras.append(('<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/><w:rPr><w:sz w:val="2"/></w:rPr>',
                          '<w:r><w:rPr><w:sz w:val="2"/></w:rPr>' + xml + "</w:r>"))
    if job.paragraph_xml is not None:
        paras.append(('<w:spacing w:before="0" w:after="0"/>', job.paragraph_xml.decode("utf-8")))
    if not paras:
        paras.append(("", ""))
    return paras


def build_batch_docx(jobs: list[SnippetJob], source: DocxPackage, out: Path, part_limit: int) -> None:
    """One DOCX with one page per job (each page sized for its job, in job order).

    Pages are separate sections whose properties sit in the job's last paragraph, so
    no extra paragraph (that could spill onto an extra page) is added.
    """
    body_parts: list[str] = []
    for i, job in enumerate(jobs):
        paras = _job_paragraphs(job)
        last = i == len(jobs) - 1
        for j, (ppr, content) in enumerate(paras):
            sect = _sect_pr(*_page_twips(job)) if (j == len(paras) - 1 and not last) else ""
            body_parts.append(f"<w:p><w:pPr>{ppr}{sect}</w:pPr>{content}</w:p>")
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS_DECL}><w:body>'
           + "".join(body_parts) + _sect_pr(*_page_twips(jobs[-1])) + "</w:body></w:document>")
    main_part, _strict = source.main_document_part(part_limit)
    main_rels = source.relationships(main_part, part_limit)
    defaults, overrides = source.content_types(part_limit)
    rel_entries: dict[str, tuple[str, str]] = {}   # rId -> (type, target relative to word/)
    for job in jobs:
        for rid, (part, rtype) in job.parts.items():
            rel_entries[rid] = (rtype, posixpath.relpath(part, "word"))
    copied: dict[str, None] = {}

    def copy_part(zf: zipfile.ZipFile, part: str, depth: int = 0) -> None:
        """Copy a part at its original path, with its relationships (recursively)."""
        if part in copied or depth > 4 or not source.has_part(part):
            return
        copied[part] = None
        with source.open_part_seekable(part, part_limit) as src, zf.open(part, "w") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        rels_name = rels_part_for(part)
        if source.has_part(rels_name):
            zf.writestr(rels_name, source.read_part(rels_name, part_limit))
            for rel in source.relationships(part, part_limit).values():
                if rel.part_name and not rel.external:
                    copy_part(zf, rel.part_name, depth + 1)
        if overrides.get(part, "").endswith("diagramData+xml"):
            # SmartArt: the pre-rendered drawing part is linked from the data part
            # (dsp:dataModelExt relId=…) through the *document's* relationships.
            data = source.read_part(part, part_limit)
            for rid in re.findall(rb'relId="([^"]+)"', data):
                rel = main_rels.get(rid.decode())
                if rel and rel.part_name and not rel.external:
                    rel_entries[rel.rel_id] = (rel.rel_type, posixpath.relpath(rel.part_name, "word"))
                    copy_part(zf, rel.part_name, depth + 1)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # Document-wide parts the fragments depend on: theme (scheme colours/fonts used by
        # DrawingML and SmartArt), styles, numbering and font table — copied once per batch.
        for rel in main_rels.values():
            if rel.rel_type.rsplit("/", 1)[-1] in _CONTEXT_PARTS and rel.part_name and not rel.external:
                rel_entries.setdefault(rel.rel_id, (rel.rel_type, posixpath.relpath(rel.part_name, "word")))
                copy_part(zf, rel.part_name)
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rIdDoc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", doc)
        for job in jobs:
            for part, _rtype in list(job.parts.values()):
                copy_part(zf, part)
        blob_rels = {}
        for job in jobs:
            for rid, (name, data, rtype) in (job.blobs or {}).items():
                if rid not in blob_rels:
                    zf.writestr("word/media/" + name, data)
                    blob_rels[rid] = f'<Relationship Id="{escape(rid)}" Type="{escape(rtype)}" Target="media/{name}"/>'
        zf.writestr("word/_rels/document.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="{escape(rid)}" Type="{escape(t)}" Target="{escape(tg)}"/>'
                              for rid, (t, tg) in rel_entries.items())
                    + "".join(blob_rels.values()) + "</Relationships>")
        part_overrides = "".join(
            f'<Override PartName="/{escape(p)}" ContentType="{escape(overrides[p])}"/>' for p in copied if p in overrides)
        exts = dict(_CT_DEFAULTS)
        for p in copied:
            ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
            if ext and ext not in exts and ext in defaults:
                exts[ext] = defaults[ext]
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    + "".join(f'<Default Extension="{escape(e)}" ContentType="{escape(c)}"/>' for e, c in exts.items())
                    + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    + part_overrides + "</Types>")


def build_snippet_docx(job: SnippetJob, source: DocxPackage, out: Path, part_limit: int) -> None:
    build_batch_docx([job], source, out, part_limit)


_PAGE_NO = re.compile(r"-(\d+)\.png$")


# ---------------------------------------------------------------- process-wide limit
# All concurrent jobs (and both languages of a pair) share one pool of renderer process
# slots, so N simultaneous conversions never start N x libreoffice_parallelism heavy
# processes. Per server process: with several uvicorn workers the total is workers x slots.
_slots_lock = threading.Lock()
_slots: threading.BoundedSemaphore | None = None
_slots_size = 0


def default_renderer_processes() -> int:
    return max(1, os.cpu_count() or 1)


def _renderer_slots(size: int) -> threading.BoundedSemaphore:
    global _slots, _slots_size
    with _slots_lock:
        if _slots is None or _slots_size != size:
            # Resized only by a configuration change (tests); in-flight holders keep the old one.
            _slots, _slots_size = threading.BoundedSemaphore(size), size
        return _slots


@contextmanager
def renderer_slot(size: int, deadline_check):
    """Hold one renderer process slot; waiting honours the job deadline."""
    sem = _renderer_slots(size)
    while not sem.acquire(timeout=0.5):
        deadline_check()
    try:
        yield
    finally:
        sem.release()


class SnippetRenderer:
    """Renders snippets in **batch documents**: one DOCX per batch, one page per snippet.

    Per batch: one LibreOffice load (``--convert-to pdf``) and one ``pdftocairo`` run for
    all pages, so the cost per figure is a page, not a process. The first page of every
    batch is a calibration object of known size (LibreOffice's inline placement offset).
    If a batch PDF does not have exactly one page per snippet (e.g. content spilled onto
    an extra page), that batch is split in halves and re-rendered, down to one snippet per
    document if needed, so pages are never attributed to the wrong figure. A single bad
    figure in a batch of 100 costs ~7 extra runs instead of 100.
    """

    def __init__(self, workdir: Path, soffice: str, pdftocairo: str, dpi: int, timeout: int, batch_size: int,
                 parallelism: int = 1, profile_root: Path | None = None, max_processes: int | None = None):
        self.workdir = workdir
        self.soffice = soffice
        self.pdftocairo = pdftocairo
        self.dpi = dpi
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.parallelism = max(1, parallelism)
        self.max_processes = max(1, max_processes or default_renderer_processes())
        # LibreOffice profiles are deep directory trees; Windows (MAX_PATH 260) needs them on a
        # short path, independent of how deep the job workspace is.
        self.profile_root = profile_root or workdir
        self.stats = {"batches": 0, "fallback_batches": 0, "pages": 0}
        for d in ("in", "pdf", "png"):
            (workdir / d).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public
    def render(self, jobs: list[SnippetJob], source: DocxPackage, part_limit: int, deadline_check) -> dict[str, Path | str]:
        """Return key -> PNG path, or key -> error string."""
        if not jobs:
            return {}
        n_batches = max(-(-len(jobs) // self.batch_size), min(self.parallelism, len(jobs)))
        batches = [jobs[i::n_batches] for i in range(n_batches)]  # round-robin: similar mix per batch
        results: dict[str, Path | str] = {}
        rnd = 0
        while batches:
            results.update(self._run_batches(batches, source, part_limit, deadline_check, prefix=f"r{rnd}_"))
            failed = [batches[idx] for idx, ok in self._batch_ok.items() if not ok]  # only len > 1
            self.stats["fallback_batches"] += len(failed)
            batches = [half for b in failed for half in (b[:len(b) // 2], b[len(b) // 2:])]
            rnd += 1
        return results

    # ------------------------------------------------------------------ internals
    def _calibration(self) -> SnippetJob:
        from ..images.asset_pipeline import _pic_run_xml
        return _calibration_job(_pic_run_xml("rIdCal", *CALIBRATION_EXTENT))

    def _run_batches(self, batches: list[list[SnippetJob]], source: DocxPackage, part_limit: int, deadline_check,
                     prefix: str) -> dict[str, Path | str]:
        results: dict[str, Path | str] = {}
        self._batch_ok: dict[int, bool] = {}
        docs: dict[int, Path] = {}
        for idx, batch in enumerate(batches):
            deadline_check()
            path = self.workdir / "in" / f"{prefix}{idx}.docx"
            try:
                build_batch_docx([self._calibration(), *batch], source, path, part_limit)
                docs[idx] = path
            except Exception as exc:  # noqa: BLE001
                for job in batch:
                    results[job.key] = f"SNIPPET_BUILD_FAILED:{type(exc).__name__}"
        self.stats["batches"] += len(docs)

        def process(idx: int) -> dict[str, Path | str]:
            deadline_check()
            batch = batches[idx]
            out: dict[str, Path | str] = {}
            with renderer_slot(self.max_processes, deadline_check):
                pdf = self._to_pdf(idx, docs[idx], prefix)
            if pdf is None or isinstance(pdf, str):
                err = pdf or "LIBREOFFICE_CONVERSION_FAILED"
                return {job.key: err for job in batch}
            with renderer_slot(self.max_processes, deadline_check):
                pages = self._rasterize(pdf, f"{prefix}{idx}")
            if isinstance(pages, str):
                return {job.key: pages for job in batch}
            if len(pages) != len(batch) + 1:
                if len(batch) > 1:
                    self._batch_ok[idx] = False  # split in halves and re-rendered
                    return {}
                return {batch[0].key: "PAGE_COUNT_MISMATCH"}
            self._batch_ok[idx] = True
            self.stats["pages"] += len(pages)
            origin = self._calibrate(pages[0])
            for job, png in zip(batch, pages[1:]):
                try:
                    out[job.key] = self._crop(png, job, origin)
                except ConverterError as exc:
                    out[job.key] = str(exc)
            return out

        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            for part in pool.map(process, list(docs)):
                results.update(part)
        return results

    def _to_pdf(self, idx: int, docx: Path, prefix: str) -> Path | str | None:
        profile = (self.profile_root / f"{uuid.uuid4().hex[:8]}{prefix}{idx}").resolve()
        cmd = [self.soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
               f"-env:UserInstallation={profile.as_uri()}", "--convert-to", "pdf",
               "--outdir", str(self.workdir / "pdf"), str(docx)]
        try:
            run_isolated(cmd, self.workdir / f"run{prefix}{idx}", self.timeout)
        except ConverterTimeout:
            return "LIBREOFFICE_TIMEOUT"
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        pdf = self.workdir / "pdf" / (docx.stem + ".pdf")
        return pdf if pdf.exists() else None

    def _rasterize(self, pdf: Path, stem: str) -> list[Path] | str:
        base = self.workdir / "png" / stem
        try:
            proc = run_isolated([self.pdftocairo, "-png", "-r", str(self.dpi), str(pdf), str(base)],
                                self.workdir / "raster", self.timeout)
        except ConverterError:
            return "RASTERIZE_TIMEOUT"
        if proc.returncode != 0:
            return "RASTERIZE_FAILED"
        pages = [(int(m.group(1)), p) for p in base.parent.glob(stem + "-*.png") if (m := _PAGE_NO.search(p.name))]
        return [p for _n, p in sorted(pages)]

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

    def _crop(self, png: Path, job: SnippetJob, origin: tuple[int, int] | None) -> Path:
        with Image.open(png) as im:
            im.load()
        # Edges from one greyscale copy; only the cropped region is converted to RGB.
        ink = ImageOps.invert(im.convert("L")).getbbox()
        if ink is None:
            raise ConverterError("EMPTY_RENDER")
        if job.mode == "exact" and job.extent_emu:
            if origin is None:
                raise ConverterError("CALIBRATION_FAILED")
            x0, y0 = origin
            w = max(1, round(job.extent_emu[0] / 914400 * self.dpi))
            h = max(1, round(job.extent_emu[1] / 914400 * self.dpi))
            if x0 + w > im.width + 1 or y0 + h > im.height + 1:
                raise ConverterError("PAGE_TOO_SMALL")  # object would be clipped: never return a cut figure
            box = (x0, y0, min(x0 + w, im.width), min(y0 + h, im.height))
            if ink[0] < x0 - 2 or ink[1] < y0 - 2 or ink[2] > box[2] + 2 or ink[3] > box[3] + 2:
                # Content outside the object's extent (e.g. shapes overflowing a group): never clip it.
                box = (min(ink[0], x0), min(ink[1], y0), max(ink[2], box[2]), max(ink[3], box[3]))
        else:
            pad = max(2, self.dpi // 48)
            box = (max(0, ink[0] - pad), max(0, ink[1] - pad), min(im.width, ink[2] + pad), min(im.height, ink[3] + pad))
        out = self.workdir / "png" / f"{job.key}.final.png"
        region = im.crop(box)
        save_png_lossless(region if region.mode == "RGB" else region.convert("RGB"), out)
        return out


def save_png_lossless(img: Image.Image, out: Path) -> None:
    """Smallest lossless PNG for rendered figures: greyscale when every pixel is grey
    (R == G == B, typical for chemical structures; about half the size), otherwise RGB."""
    r, g, b = img.split()
    if ImageChops.difference(r, g).getbbox() is None and ImageChops.difference(g, b).getbbox() is None:
        img = r  # exact: no pixel changes
    img.save(out, "PNG", compress_level=9)
