# Supported-elements matrix

Legend: **Detected** = analyzer finds and counts it (Phase 2, tested).
**Rendered** = converted to HTML (Phase 4+). ❌ = not yet. "Fail-safe" = the
plan when faithful rendering is impossible: explicit error, never silent loss.

| Element | OOXML | Detected | Rendered | Notes / plan |
|---|---|---|---|---|
| Paragraphs, runs, text | `w:p`, `w:r`, `w:t`, `w:tab`, `w:br`, `w:noBreakHyphen`, `w:softHyphen` | ✅ | ❌ | |
| Run formatting (b/i/u, sub/sup, font, size, colour, highlight) | `w:rPr` | ✅ (sub/sup counted) | ❌ | |
| RTL paragraphs / runs / tables | `w:bidi`, `w:rtl`, `w:bidiVisual` | ✅ | ❌ | `dir="rtl" lang="ar"` on blocks; text never reversed |
| Bidi overrides | `w:dir`, `w:bdo` | ✅ | ❌ | |
| List numbering (incl. Arabic formats) | `w:numPr`, numbering.xml, style numbering | ✅ resolved labels | ❌ | decimal, roman, letters, `hindiNumbers`, `arabicAlpha`, `arabicAbjad`; others reported |
| Tables, nested tables | `w:tbl` | ✅ (depth tracked) | ❌ | |
| Merged cells | `w:gridSpan`, `w:vMerge`, `w:hMerge` | ✅ | ❌ | |
| Content controls / customXml | `w:sdt`, `w:customXml` | ✅ expanded | ❌ | |
| Hyperlinks | `w:hyperlink`, `HYPERLINK` field | ✅ | ❌ | target never fetched |
| Fields | `w:fldSimple`, `w:fldChar`/`w:instrText` | ✅ types histogram | ❌ | results used for matching (SEQ claim numbers) |
| Symbols | `w:sym` | ✅ + font | ❌ | Symbol/Wingdings PUA → Unicode mapping table required |
| Symbol-font text (Greek via "Symbol") | `w:rFonts` | ✅ risk | ❌ | mapping required; never left as Latin letters |
| Inline images | `wp:inline` / `pic:pic` / `a:blip` | ✅ | ❌ | |
| Floating images | `wp:anchor` | ✅ | ❌ | position approximated in flow (documented) |
| Grouped drawings, canvases, shapes | `wpg:wgp`, `wpc:wpc`, `wps:wsp` | ✅ | ❌ | rasterize the group to keep relative positions |
| Text boxes | `w:txbxContent` (wps / VML) | ✅ | ❌ | |
| AlternateContent | `mc:Choice` / `mc:Fallback` | ✅ Word-style branch choice | ❌ | fallback not double-counted |
| VML images / shapes / groups | `w:pict`, `v:*` | ✅ | ❌ | |
| SVG (with PNG fallback) | `asvg:svgBlip` | ✅ | ❌ | sanitize before embedding |
| OLE objects (ChemDraw, Equation 3.0, …) | `w:object`, `o:OLEObject` | ✅ ProgID, preview presence | ❌ | rendered from preview image; never executed |
| OMML equations | `m:oMath`, `m:oMathPara` | ✅ + construct histogram | ❌ | OMML→MathML; image fallback |
| Legacy EQ fields | `EQ` field | ✅ risk | ❌ | |
| Charts / SmartArt | `c:chart`, `dgm:relIds` | ✅ risk | ❌ | fail-safe unless a fallback image exists |
| altChunk / subDoc | `w:altChunk`, `w:subDoc` | ✅ risk | ❌ | fail-safe |
| Tracked changes | `w:ins`, `w:del`, `w:move*`, `*PrChange` | ✅ risk | ❌ | **policy decision required** (see checklist) |
| Comments, footnote/endnote refs | | ✅ | ❌ | |
| Linked (external) images | `a:blip r:link`, `INCLUDEPICTURE` | ✅ risk | ❌ | cannot be embedded → fail-safe |
| Unknown elements | any | ✅ reported by qname | — | |

## Image formats

| Format | Detected (magic bytes + header dims) | Embedding plan |
|---|---|---|
| PNG, JPEG, GIF, BMP, WebP | ✅ | as-is |
| SVG | ✅ (active content / external refs / entities flagged) | sanitized |
| TIFF | ✅ (frames counted) | → PNG |
| EMF / WMF | ✅ | isolated converter → PNG/SVG |
| JPEG XR (`.wdp`), PDF, unknown | ✅ flagged unsupported | fail-safe |

## Explicitly unsupported (fail-safe)
ISO/IEC 29500 Strict documents, macro-enabled documents, password-protected
(CFB-encrypted) files, legacy `.doc`.
