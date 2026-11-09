"""Raw-OOXML synthetic DOCX builder for tests and benchmarks.

python-docx cannot author OMML, VML, grouped drawings, OLE objects or
AlternateContent, so fixtures are written as raw WordprocessingML. The XML
mirrors what Microsoft Word produces for each structure.

LIMITATION: synthetic fixtures prove the parser handles the *structures*; they
do not prove coverage of every variant produced by real patent authoring tools.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

NS_DECL = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
    'mc:Ignorable="w14"'
)
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
EMU_PER_PX = 9525


# --------------------------------------------------------------------------- images
def png_bytes(w: int = 120, h: int = 80, alpha: bool = True, noise: bool = False, seed: int = 0) -> bytes:
    if noise:
        img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3) if seed == 0 else _seeded(w * h * 3, seed))
    else:
        img = Image.new("RGBA" if alpha else "RGB", (w, h), (255, 255, 255, 0) if alpha else (255, 255, 255))
        d = ImageDraw.Draw(img)
        # A benzene-like hexagon so the image is a plausible "chemical drawing".
        cx, cy, r = w // 2, h // 2, min(w, h) // 3
        pts = [(cx + r * c, cy + r * s) for c, s in ((1, 0), (0.5, 0.87), (-0.5, 0.87), (-1, 0), (-0.5, -0.87), (0.5, -0.87))]
        d.polygon(pts, outline=(0, 0, 0, 255) if alpha else (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1 if noise else 6)
    return buf.getvalue()


def _seeded(n: int, seed: int) -> bytes:
    import random
    rnd = random.Random(seed)
    return rnd.randbytes(n)


def raster_bytes(fmt: str, w: int = 100, h: int = 60) -> bytes:
    img = Image.new("RGB", (w, h), (240, 240, 255))
    ImageDraw.Draw(img).line((0, 0, w, h), fill=(0, 0, 0), width=2)
    buf = io.BytesIO()
    if fmt == "GIF":
        img = img.convert("P")
    img.save(buf, fmt)
    return buf.getvalue()


def emf_bytes(w_px: int = 200, h_px: int = 100) -> bytes:
    """Minimal valid EMF: EMR_HEADER + EMR_EOF."""
    frame = (0, 0, int(w_px * 26.46), int(h_px * 26.46))  # 0.01 mm at 96 dpi
    total = 88 + 20
    header = struct.pack(
        "<II4i4iIIIIHHIII2i2i",
        1, 88, 0, 0, w_px - 1, h_px - 1, *frame, 0x464D4520, 0x10000, total, 2, 1, 0, 0, 0, 0,
        1920, 1080, 508, 286,
    )
    eof = struct.pack("<IIIII", 14, 20, 0, 16, 20)
    return header + eof


def wmf_bytes(w: int = 1440, h: int = 720) -> bytes:
    """Minimal placeable WMF (Aldus header + META_HEADER + META_EOF)."""
    words = struct.unpack("<10H", struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, w, h, 1440, 0))
    checksum = 0
    for word in words:
        checksum ^= word
    placeable = struct.pack("<IH4hHIH", 0x9AC6CDD7, 0, 0, 0, w, h, 1440, 0, checksum)
    meta = struct.pack("<HHHIHIH", 1, 9, 0x0300, 9 + 3, 0, 3, 0)
    eof = struct.pack("<IH", 3, 0)
    return placeable + meta + eof


SVG_SAMPLE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" viewBox="0 0 120 80">'
    b'<polygon points="60,10 95,30 95,60 60,75 25,60 25,30" fill="none" stroke="black"/></svg>'
)


# --------------------------------------------------------------------------- XML snippets
def t(text: str) -> str:
    return f'<w:t xml:space="preserve">{escape(text)}</w:t>'


def run(text: str = "", *, b: bool = False, i: bool = False, u: bool = False, vert: str | None = None,
        rtl: bool = False, font: str | None = None, color: str | None = None, size: int | None = None) -> str:
    props = []
    if font:
        props.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
    if b:
        props.append("<w:b/><w:bCs/>")
    if i:
        props.append("<w:i/><w:iCs/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if u:
        props.append('<w:u w:val="single"/>')
    if vert:
        props.append(f'<w:vertAlign w:val="{vert}"/>')
    if rtl:
        props.append('<w:rtl/><w:lang w:bidi="ar-SA"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f"<w:r>{rpr}{t(text)}</w:r>"


def para(*content: str, rtl: bool = False, num: tuple[int, int] | None = None, jc: str | None = None,
         style: str | None = None) -> str:
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if num:
        ppr.append(f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>')
    if rtl:
        ppr.append("<w:bidi/>")
    if jc:
        ppr.append(f'<w:jc w:val="{jc}"/>')
    body = "".join(c if c.startswith("<") else run(c, rtl=rtl) for c in content)
    return f"<w:p>{'<w:pPr>' + ''.join(ppr) + '</w:pPr>' if ppr else ''}{body}</w:p>"


def _pic(rid: str, cx: int, cy: int, pid: int, svg_rid: str | None = None) -> str:
    ext = ""
    if svg_rid:
        ext = ('<a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
               f'<asvg:svgBlip r:embed="{svg_rid}"/></a:ext></a:extLst>')
    return (
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{pid}" name="Picture {pid}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}">{ext}</a:blip><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
    )


def inline_image(rid: str, w_px: int, h_px: int, pid: int, svg_rid: str | None = None) -> str:
    cx, cy = w_px * EMU_PER_PX, h_px * EMU_PER_PX
    return (
        f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{pid}" name="Picture {pid}"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"{_pic(rid, cx, cy, pid, svg_rid)}</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _anchor(graphic: str, cx: int, cy: int, pid: int) -> str:
    return (
        f'<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="1" '
        f'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="column"><wp:posOffset>0</wp:posOffset></wp:positionH>'
        '<wp:positionV relativeFrom="paragraph"><wp:posOffset>0</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:wrapSquare wrapText="bothSides"/>'
        f'<wp:docPr id="{pid}" name="Shape {pid}"/><a:graphic>{graphic}</a:graphic></wp:anchor>'
    )


def anchored_image(rid: str, w_px: int, h_px: int, pid: int) -> str:
    cx, cy = w_px * EMU_PER_PX, h_px * EMU_PER_PX
    g = f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">{_pic(rid, cx, cy, pid)}</a:graphicData>'
    return f"<w:r><w:drawing>{_anchor(g, cx, cy, pid)}</w:drawing></w:r>"


def textbox(text: str, pid: int, rtl: bool = False) -> str:
    inner = para(text, rtl=rtl)
    cx, cy = 2000000, 600000
    g = ('<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
         f'<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr/><wps:txbx><w:txbxContent>{inner}</w:txbxContent></wps:txbx>'
         '<wps:bodyPr/></wps:wsp></a:graphicData>')
    return (
        '<w:r><mc:AlternateContent><mc:Choice Requires="wps">'
        f"<w:drawing>{_anchor(g, cx, cy, pid)}</w:drawing></mc:Choice>"
        f'<mc:Fallback><w:pict><v:shape style="width:157pt;height:47pt"><v:textbox><w:txbxContent>{inner}'
        "</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r>"
    )


def grouped_drawing(rid1: str, rid2: str, label: str, pid: int) -> str:
    cx, cy = 3000000, 1500000
    shape = (f'<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr/><wps:txbx><w:txbxContent>{para(label)}'
             "</w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp>")
    g = ('<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">'
         f'<wpg:wgp><wpg:cNvGrpSpPr/><wpg:grpSpPr/>{_pic(rid1, cx // 2, cy, pid + 1)}{_pic(rid2, cx // 2, cy, pid + 2)}'
         f"{shape}</wpg:wgp></a:graphicData>")
    return (
        '<w:r><mc:AlternateContent><mc:Choice Requires="wpg">'
        f"<w:drawing>{_anchor(g, cx, cy, pid)}</w:drawing></mc:Choice>"
        f'<mc:Fallback><w:pict><v:group style="width:236pt;height:118pt"><v:shape><v:imagedata r:id="{rid1}"/>'
        f'</v:shape><v:shape><v:imagedata r:id="{rid2}"/></v:shape></v:group></w:pict></mc:Fallback>'
        "</mc:AlternateContent></w:r>"
    )


def vml_image(rid: str) -> str:
    return (f'<w:r><w:pict><v:shape id="_x0000_s1" type="#_x0000_t75" style="width:90pt;height:45pt">'
            f'<v:imagedata r:id="{rid}" o:title=""/></v:shape></w:pict></w:r>')


def ole_object(preview_rid: str, ole_rid: str, progid: str = "ChemDraw.Document.6.0") -> str:
    return (
        '<w:r><w:object w:dxaOrig="2880" w:dyaOrig="1440"><v:shape id="_x0000_i1025" type="#_x0000_t75" '
        f'style="width:144pt;height:72pt" o:ole=""><v:imagedata r:id="{preview_rid}" o:title=""/></v:shape>'
        f'<o:OLEObject Type="Embed" ProgID="{progid}" ShapeID="_x0000_i1025" DrawAspect="Content" '
        f'ObjectID="_1700000001" r:id="{ole_rid}"/></w:object></w:r>'
    )


def mr(text: str) -> str:
    return f"<m:r><m:t>{escape(text)}</m:t></m:r>"


def omml_fraction(num: str, den: str) -> str:
    return f"<m:f><m:num>{mr(num)}</m:num><m:den>{mr(den)}</m:den></m:f>"


def omml_sup(base: str, sup: str) -> str:
    return f"<m:sSup><m:e>{mr(base)}</m:e><m:sup>{mr(sup)}</m:sup></m:sSup>"


def omml_sub(base: str, sub: str) -> str:
    return f"<m:sSub><m:e>{mr(base)}</m:e><m:sub>{mr(sub)}</m:sub></m:sSub>"


def omml_rad(x: str) -> str:
    return f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>{mr(x)}</m:e></m:rad>'


def omml_nary(chr_: str, lo: str, hi: str, body: str) -> str:
    return (f'<m:nary><m:naryPr><m:chr m:val="{chr_}"/></m:naryPr><m:sub>{mr(lo)}</m:sub>'
            f"<m:sup>{mr(hi)}</m:sup><m:e>{mr(body)}</m:e></m:nary>")


def omml_matrix(rows: list[list[str]]) -> str:
    body = "".join("<m:mr>" + "".join(f"<m:e>{mr(c)}</m:e>" for c in r) + "</m:mr>" for r in rows)
    return f"<m:d><m:e><m:m>{body}</m:m></m:e></m:d>"


def omath(*parts: str) -> str:
    return "<m:oMath>" + "".join(parts) + "</m:oMath>"


def omath_para(*parts: str) -> str:
    return f"<w:p><m:oMathPara>{omath(*parts)}</m:oMathPara></w:p>"


def cell(*content: str, span: int = 1, vmerge: str | None = None, rtl: bool = False) -> str:
    pr = []
    if span > 1:
        pr.append(f'<w:gridSpan w:val="{span}"/>')
    if vmerge:
        pr.append('<w:vMerge w:val="restart"/>' if vmerge == "restart" else "<w:vMerge/>")
    body = "".join(c if c.startswith("<w:p") or c.startswith("<w:tbl") else para(c, rtl=rtl) for c in content)
    if not body.endswith("</w:p>"):
        body += "<w:p/>"
    return f"<w:tc>{'<w:tcPr>' + ''.join(pr) + '</w:tcPr>' if pr else ''}{body}</w:tc>"


def table(rows: list[list[str]], cols: int, rtl: bool = False) -> str:
    pr = '<w:tblPr><w:tblW w:w="0" w:type="auto"/>' + ("<w:bidiVisual/>" if rtl else "") + \
        '<w:tblBorders><w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>'
    grid = "<w:tblGrid>" + '<w:gridCol w:w="2000"/>' * cols + "</w:tblGrid>"
    return f"<w:tbl>{pr}{grid}" + "".join("<w:tr>" + "".join(r) + "</w:tr>" for r in rows) + "</w:tbl>"


def hyperlink(rid: str, text: str) -> str:
    return f'<w:hyperlink r:id="{rid}">{run(text, u=True, color="0563C1")}</w:hyperlink>'


def sym(font: str, char: str) -> str:
    return f'<w:r><w:sym w:font="{font}" w:char="{char}"/></w:r>'


def tracked_insert(text: str) -> str:
    return f'<w:ins w:id="901" w:author="Examiner" w:date="2026-01-01T00:00:00Z">{run(text)}</w:ins>'


def tracked_delete(text: str) -> str:
    return (f'<w:del w:id="902" w:author="Examiner" w:date="2026-01-01T00:00:00Z"><w:r>'
            f'<w:delText xml:space="preserve">{escape(text)}</w:delText></w:r></w:del>')


def seq_field(value: str) -> str:
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> SEQ Claim \\* ARABIC </w:instrText></w:r>'
            f'<w:r><w:fldChar w:fldCharType="separate"/></w:r>{run(value)}<w:r><w:fldChar w:fldCharType="end"/></w:r>')


# --------------------------------------------------------------------------- package
@dataclass
class DocxBuilder:
    body: list[str] = field(default_factory=list)
    _rels: list[tuple[str, str, str, bool]] = field(default_factory=list)
    _media: list[tuple[str, bytes]] = field(default_factory=list)
    numbering_xml: str | None = None
    extra_parts: dict[str, bytes] = field(default_factory=dict)
    extra_content_types: list[str] = field(default_factory=list)
    _next_pid: int = 1

    def pid(self) -> int:
        self._next_pid += 3
        return self._next_pid

    def _rel(self, rtype: str, target: str, external: bool = False) -> str:
        rid = f"rId{len(self._rels) + 10}"
        self._rels.append((rid, rtype, target, external))
        return rid

    def add_media(self, data: bytes, ext: str) -> str:
        name = f"image{len(self._media) + 1}.{ext}"
        self._media.append((f"word/media/{name}", data))
        return self._rel(RT + "image", f"media/{name}")

    def add_ole(self, data: bytes | None = None) -> str:
        name = f"oleObject{sum(1 for n in self.extra_parts if 'embeddings' in n) + 1}.bin"
        self.extra_parts[f"word/embeddings/{name}"] = data or (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + os.urandom(2040))
        return self._rel(RT + "oleObject", f"embeddings/{name}")

    def add_hyperlink(self, url: str) -> str:
        return self._rel(RT + "hyperlink", url, external=True)

    def add_linked_image(self, url: str) -> str:
        return self._rel(RT + "image", url, external=True)

    def add(self, *xml: str) -> "DocxBuilder":
        self.body.extend(xml)
        return self

    def use_numbering(self, fmt: str = "decimal", lvl_text: str = "%1.") -> int:
        self.numbering_xml = (
            '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
            f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{lvl_text}"/><w:suff w:val="tab"/></w:lvl>'
            '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="(%2)"/></w:lvl>'
            '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
        )
        return 1

    def document_xml(self) -> bytes:
        sect = '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {NS_DECL}><w:body>'
                + "".join(self.body) + sect + "</w:body></w:document>").encode("utf-8")

    def build(self, path: Path | str, document_xml: bytes | None = None,
              compression: int = zipfile.ZIP_DEFLATED) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rels = [("rId1", RT + "styles", "styles.xml", False)] + self._rels
        if self.numbering_xml:
            rels.append(("rId2", RT + "numbering", "numbering.xml", False))
        ct_overrides = [
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
        ]
        if self.numbering_xml:
            ct_overrides.append('<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>')
        ct_overrides += self.extra_content_types
        defaults = "".join(
            f'<Default Extension="{e}" ContentType="{c}"/>' for e, c in [
                ("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("png", "image/png"), ("jpeg", "image/jpeg"), ("jpg", "image/jpeg"), ("gif", "image/gif"),
                ("tiff", "image/tiff"), ("emf", "image/x-emf"), ("wmf", "image/x-wmf"), ("svg", "image/svg+xml"),
                ("bin", "application/vnd.openxmlformats-officedocument.oleObject"),
            ])
        with zipfile.ZipFile(path, "w", compression) as zf:
            zf.writestr("[Content_Types].xml",
                        '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                        + defaults + "".join(ct_overrides) + "</Types>")
            zf.writestr("_rels/.rels",
                        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        f'<Relationship Id="rId1" Type="{RT}officeDocument" Target="word/document.xml"/></Relationships>')
            zf.writestr("word/document.xml", document_xml if document_xml is not None else self.document_xml())
            zf.writestr("word/styles.xml",
                        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
                        '<w:style w:type="paragraph" w:styleId="ClaimList"><w:name w:val="Claim List"/><w:basedOn w:val="Normal"/>'
                        '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style></w:styles>')
            if self.numbering_xml:
                zf.writestr("word/numbering.xml", self.numbering_xml)
            zf.writestr("word/_rels/document.xml.rels",
                        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        + "".join(
                            f'<Relationship Id="{rid}" Type="{rt}" Target="{escape(tg)}"'
                            + (' TargetMode="External"' if ext else "") + "/>" for rid, rt, tg, ext in rels)
                        + "</Relationships>")
            for name, data in self._media:
                zf.writestr(name, data, compress_type=zipfile.ZIP_STORED if name.endswith((".png", ".jpeg", ".gif")) else compression)
            for name, data in self.extra_parts.items():
                zf.writestr(name, data)
        return path
