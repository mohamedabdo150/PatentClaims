"""Canonical internal document model (renderer-independent).

Built by ``extraction.model_builder`` from WordprocessingML and consumed by
``renderers.html_renderer``. Order is preserved everywhere (``blocks`` and
``inlines`` are ordered lists) and text is stored exactly as written in the
document (the only transformation is the documented Symbol-font mapping).
RTL is a presentation attribute (``direction``); characters are never reordered.

Visual assets are referenced by integer ids into ``assets.AssetRegistry`` so
image bytes are never held by the model: each asset is converted to a file on
disk once and Base64-streamed into the HTML only when its claim is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

Direction = Literal["ltr", "rtl"]


@dataclass(frozen=True)
class RunFormat:
    bold: bool = False
    italic: bool = False
    underline: str | None = None          # css: single->solid, double, dotted, ...
    strike: bool = False
    vertical_align: Literal["baseline", "superscript", "subscript"] = "baseline"
    font_family: str | None = None        # emitted only when different from the document default
    font_size_pt: float | None = None     # idem
    color: str | None = None              # "#RRGGBB"
    highlight: str | None = None          # css colour
    caps: bool = False
    small_caps: bool = False


@dataclass
class TextRun:
    text: str
    fmt: RunFormat = field(default_factory=RunFormat)
    kind: Literal["text"] = "text"


@dataclass
class LineBreak:
    kind: Literal["break"] = "break"


@dataclass
class Hyperlink:
    inlines: list["Inline"]
    href: str | None                       # only http/https/mailto survive
    kind: Literal["hyperlink"] = "hyperlink"


@dataclass
class DirSpan:
    inlines: list["Inline"]
    direction: Direction
    override: bool = False                 # w:bdo
    kind: Literal["dir"] = "dir"


@dataclass
class Image:
    asset_id: int
    width_px: float | None
    height_px: float | None
    alt: str = ""
    floating: bool = False                 # wp:anchor -> kept at its position in the text flow
    source: Literal["media", "drawing", "vml", "ole_preview", "equation"] = "media"
    kind: Literal["image"] = "image"


@dataclass
class Equation:
    display: bool
    mathml: str | None                     # MathML markup when the conversion is faithful
    fallback_asset_id: int | None = None   # rendered image of the original OMML
    alt: str = ""                          # linear text of the equation
    text_chars: int = 0                    # OMML characters represented by this equation
    kind: Literal["equation"] = "equation"


@dataclass
class TextBox:
    blocks: list["Block"]
    floating: bool
    width_px: float | None = None
    bordered: bool = False
    kind: Literal["textbox"] = "textbox"


@dataclass
class NoteReference:
    mark: str                              # displayed footnote/endnote number
    note_type: Literal["footnote", "endnote"]
    kind: Literal["noteref"] = "noteref"


Inline = Union[TextRun, LineBreak, Hyperlink, DirSpan, Image, Equation, TextBox, NoteReference]


@dataclass
class ParagraphFormat:
    direction: Direction = "ltr"
    alignment: str | None = None           # css text-align
    margin_start_pt: float | None = None
    margin_end_pt: float | None = None
    text_indent_pt: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    line_height: float | None = None       # multiple of single spacing
    numbering_label: str | None = None


@dataclass
class Paragraph:
    inlines: list[Inline] = field(default_factory=list)
    fmt: ParagraphFormat = field(default_factory=ParagraphFormat)
    kind: Literal["paragraph"] = "paragraph"


@dataclass
class CellBorder:
    style: str                             # css border-style
    width_px: float
    color: str


@dataclass
class TableCell:
    blocks: list["Block"] = field(default_factory=list)
    col_span: int = 1
    row_span: int = 1
    borders: dict[str, CellBorder | None] = field(default_factory=dict)   # top/bottom/start/end
    background: str | None = None
    vertical_align: str | None = None
    width_pt: float | None = None


@dataclass
class TableRow:
    cells: list[TableCell] = field(default_factory=list)


@dataclass
class Table:
    rows: list[TableRow] = field(default_factory=list)
    direction: Direction = "ltr"
    kind: Literal["table"] = "table"


Block = Union[Paragraph, Table]


@dataclass
class ClaimCounters:
    """Integrity counters filled by the builder and checked against the analyzer."""
    text_chars: int = 0              # text nodes represented as text in the model
    text_chars_rasterized: int = 0   # text inside drawings rendered to images
    text_chars_removed: int = 0      # removed by an explicit policy (accepted deletions, hidden text)
    image_refs: int = 0              # image relationship references accounted for
    images: int = 0                  # Image inlines (embedded assets)
    equations: int = 0
    equations_mathml: int = 0
    equations_image: int = 0
    tables: int = 0
    tables_rasterized: int = 0
    textboxes: int = 0
    drawings_rasterized: int = 0
    floating_objects: int = 0
    symbol_chars_mapped: int = 0


@dataclass
class ContentIssue:
    code: str
    severity: Literal["error", "warning"]
    message: str
    count: int = 1


@dataclass
class Claim:
    number: str                            # normalized pairing key
    number_raw: str                        # exactly as written
    language: Literal["en", "ar"]
    blocks: list[Block] = field(default_factory=list)
    counters: ClaimCounters = field(default_factory=ClaimCounters)
    issues: list[ContentIssue] = field(default_factory=list)
