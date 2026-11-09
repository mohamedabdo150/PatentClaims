# Supported-elements matrix

Rendering methods: **HTML** = semantic HTML, **MathML**, **Image** = embedded
Base64 image (as-is or converted), **Raster** = original XML rendered by isolated
LibreOffice to PNG with exact extent, **Error** = conversion fails with
`UNSUPPORTED_CONTENT` (fail-safe, never dropped silently).

All rows are covered by automated tests on synthetic documents. Real-document
coverage is pending.

| Element | OOXML | Output |
|---|---|---|
| Paragraphs, runs, text, tabs, breaks | `w:p`, `w:r`, `w:t`, `w:tab`, `w:ptab`, `w:br`, `w:cr`, `w:noBreakHyphen`, `w:softHyphen` | HTML (`<p>`, tab as `white-space:pre` span holding the tab character, `<br>`) |
| Bold / italic / underline / strike / sub / sup | `w:rPr` + style chain | `<strong> <em> <u> <s> <sub> <sup>`; non-single underline as CSS |
| Font family / size / colour / highlight / caps | `w:rFonts`, `w:sz`, `w:color`, `w:highlight`, `w:shd`, `w:caps` | inline CSS **only when different from the document default** |
| Complex-script formatting | `w:bCs`, `w:iCs`, `w:szCs`, `w:rFonts/@cs` | used for RTL/Arabic runs |
| Paragraph alignment / indent / spacing / line spacing | `w:jc`, `w:ind`, `w:spacing` | inline CSS (logical start/end mapped by direction) |
| RTL paragraphs / tables | `w:bidi`, `w:bidiVisual` | `dir="rtl"` (claim wrapper `dir` + `lang`); characters never reordered |
| Bidi overrides | `w:dir`, `w:bdo` | `<span dir>`, `<bdo dir>` |
| List numbering (incl. Arabic formats) | `w:numPr`, numbering.xml, style numbering | resolved label as text (`<span class="pc-label">`) |
| Tables, nested tables | `w:tbl` (also inside `w:sdt`/`w:customXml`) | `<table class="pc-table">` |
| Merged cells | `w:gridSpan`, `w:vMerge`, `w:hMerge` | `colspan` / `rowspan` (continuation-cell content appended, never lost) |
| Borders, shading, vAlign, cell width | `w:tblBorders` (+ table style), `w:tcBorders`, `w:shd`, `w:vAlign`, `w:tcW` | inline CSS |
| Content controls / customXml / smartTag / fields | `w:sdt`, `w:customXml`, `w:smartTag`, `w:fldSimple`, complex fields | content / field results rendered; instructions skipped |
| Hyperlinks | `w:hyperlink` | `<a href rel="noopener noreferrer">` for http/https/mailto; other schemes → text + warning |
| Symbol font (Greek via "Symbol") | `w:sym`, Symbol-font runs | mapped to Unicode (α, β, ≥, →, …) |
| Dingbat fonts | Wingdings etc. | character kept with its font + warning (error under `strictValidation`) |
| Footnote / endnote references | `w:footnoteReference` | `<sup class="pc-noteref">n</sup>` (note text outside the claim; warning) |
| Inline images | `wp:inline` + `pic:pic` | Image at the Word extent |
| Floating images | `wp:anchor` | Image in flow at its anchor, class `pc-floating` |
| SVG (with PNG fallback) | `asvg:svgBlip` | sanitized SVG (falls back to the PNG blip) |
| PNG / JPEG / GIF / WebP | media parts | Image as-is (validated) |
| BMP / TIFF (multi-frame) | media parts | Image → PNG (all frames stacked) |
| EMF / WMF | media parts | Raster |
| Text boxes | `wps:txbx`, `v:textbox` | `<div class="pc-textbox">` with the box content as HTML |
| Grouped drawings, canvases, shapes | `wpg:wgp`, `wpc:wpc`, `wps:wsp` | Raster (grouping and relative positions preserved) |
| VML pictures / shapes / groups | `w:pict`, `v:*` | Image (single picture) or Raster |
| OLE objects (ChemDraw, Equation 3.0, …) | `w:object`, `o:OLEObject` | Image of the stored preview (never executed; warning) |
| OLE without preview | | Error |
| OMML equations | `m:oMath`, `m:oMathPara` | MathML (fractions, scripts, pre-scripts, radicals, n-ary, delimiters, matrices, accents, bars, boxes, eqArr, functions, groupChr, limits, phantoms); otherwise Raster from the original OMML; `equation_mode: image` renders all as images |
| Tracked changes | `w:ins`, `w:del`, `w:move*`, `*PrChange` | Error (policy `reject`, default) or accepted view (`accept`) |
| Hidden text | `w:vanish` | rendered (warning) or dropped (`hidden_text: drop`, counted) |
| Linked (external) images | `a:blip r:link`, `INCLUDEPICTURE` without cached picture | Error |
| Charts / SmartArt | `c:chart`, `dgm:relIds` | Error |
| altChunk / subDoc / ActiveX | | Error |
| Unknown elements | | Error if they carry text or images; ignored with a warning if empty |

## Explicitly unsupported (rejected before processing)
ISO/IEC 29500 Strict documents, macro-enabled documents, password-protected
(CFB-encrypted) files, legacy `.doc`.
