"""Canonical internal document model (renderer-independent).

Populated by the Phase 3 claim extractor and consumed by the Phase 4 HTML
renderer. The model keeps document order (``Claim.blocks`` and
``Paragraph.inlines`` are ordered lists) and never flattens content to plain
text. Every visual asset is referenced through ``MediaRegistry`` so that each
asset is read, converted and Base64-encoded exactly when its claim is
rendered, then released.

Text is stored exactly as in ``w:t`` (no normalization, no reordering): RTL is
a presentation attribute (``direction``), never a transformation of characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Union

Direction = Literal["ltr", "rtl"]


class AssetKind(str, Enum):
    RASTER = "raster"          # png/jpeg/gif/bmp/webp/tiff
    VECTOR_SVG = "svg"
    METAFILE = "metafile"      # emf/wmf -> needs isolated conversion
    OLE_PREVIEW = "ole_preview"
    RENDERED = "rendered"      # produced by the converter (equation/group rasterization)


@dataclass
class MediaAsset:
    asset_id: str              # stable id, e.g. part name
    part_name: str | None      # source part inside the DOCX (None for rendered assets)
    kind: AssetKind
    source_format: str
    size_bytes: int
    width_px: int | None = None
    height_px: int | None = None


@dataclass
class MediaRegistry:
    assets: dict[str, MediaAsset] = field(default_factory=dict)

    def register(self, asset: MediaAsset) -> str:
        self.assets.setdefault(asset.asset_id, asset)
        return asset.asset_id


@dataclass
class RunFormat:
    bold: bool = False
    italic: bool = False
    underline: str | None = None          # single/double/...
    strike: bool = False
    vertical_align: Literal["baseline", "superscript", "subscript"] = "baseline"
    font_family: str | None = None
    font_size_half_points: int | None = None
    color: str | None = None              # hex RRGGBB
    highlight: str | None = None
    rtl: bool = False
    symbol_font: str | None = None        # set when text uses a symbol font mapping


@dataclass
class TextRun:
    text: str
    fmt: RunFormat = field(default_factory=RunFormat)
    kind: Literal["text"] = "text"


@dataclass
class Break:
    break_type: Literal["line", "page", "column", "tab"] = "line"
    kind: Literal["break"] = "break"


@dataclass
class Hyperlink:
    runs: list[TextRun]
    target: str | None                    # external URL (never fetched) or None
    anchor: str | None = None
    kind: Literal["hyperlink"] = "hyperlink"


@dataclass
class InlineImage:
    asset_id: str
    width_emu: int | None
    height_emu: int | None
    alt_text: str = ""
    floating: bool = False                # wp:anchor (position approximated in flow)
    kind: Literal["image"] = "image"


@dataclass
class Equation:
    omml_xml: bytes                       # original OMML subtree, kept for faithful conversion
    display: bool                         # m:oMathPara vs inline m:oMath
    mathml: str | None = None             # filled by the equation converter
    fallback_asset_id: str | None = None  # rendered image when MathML is not faithful
    kind: Literal["equation"] = "equation"


@dataclass
class Drawing:
    """Grouped shapes / canvases / text boxes that must keep their visual grouping."""
    source: Literal["drawingml", "vml"]
    floating: bool
    child_asset_ids: list[str] = field(default_factory=list)
    text_blocks: list["Block"] = field(default_factory=list)
    rendered_asset_id: str | None = None
    width_emu: int | None = None
    height_emu: int | None = None
    kind: Literal["drawing"] = "drawing"


@dataclass
class EmbeddedObject:
    prog_id: str
    preview_asset_id: str | None          # OLE objects are rendered from their preview only
    kind: Literal["object"] = "object"


Inline = Union[TextRun, Break, Hyperlink, InlineImage, Equation, Drawing, EmbeddedObject]


@dataclass
class ParagraphFormat:
    direction: Direction = "ltr"
    alignment: Literal["left", "right", "center", "justify", "start", "end"] | None = None
    indent_left_twips: int | None = None
    indent_right_twips: int | None = None
    indent_first_line_twips: int | None = None
    spacing_before_twips: int | None = None
    spacing_after_twips: int | None = None
    line_spacing: float | None = None
    numbering_label: str | None = None    # resolved w:numPr label (e.g. "1.")
    list_level: int | None = None
    style_id: str | None = None


@dataclass
class Paragraph:
    inlines: list[Inline] = field(default_factory=list)
    fmt: ParagraphFormat = field(default_factory=ParagraphFormat)
    kind: Literal["paragraph"] = "paragraph"


@dataclass
class TableCell:
    blocks: list["Block"] = field(default_factory=list)
    col_span: int = 1
    row_span: int = 1                     # computed from w:vMerge
    background: str | None = None
    vertical_align: str | None = None
    borders: dict[str, str] = field(default_factory=dict)


@dataclass
class TableRow:
    cells: list[TableCell] = field(default_factory=list)
    is_header: bool = False


@dataclass
class Table:
    rows: list[TableRow] = field(default_factory=list)
    direction: Direction = "ltr"
    column_widths_twips: list[int] = field(default_factory=list)
    kind: Literal["table"] = "table"


Block = Union[Paragraph, Table]


@dataclass
class ClaimDiagnostics:
    images_detected: int = 0
    images_embedded: int = 0
    equations_detected: int = 0
    equations_rendered: int = 0
    tables_detected: int = 0
    tables_rendered: int = 0
    unsupported: dict[str, int] = field(default_factory=dict)
    unicode_warnings: int = 0


@dataclass
class Claim:
    number: str                           # normalized pairing key
    number_raw: str                       # exactly as written
    language: Literal["en", "ar"]
    blocks: list[Block] = field(default_factory=list)
    diagnostics: ClaimDiagnostics = field(default_factory=ClaimDiagnostics)


@dataclass
class DocumentMetadata:
    language: Literal["en", "ar"]
    source_name: str
    sha256: str
    profile: str


@dataclass
class Document:
    metadata: DocumentMetadata
    media: MediaRegistry = field(default_factory=MediaRegistry)
    claims: list[Claim] = field(default_factory=list)
