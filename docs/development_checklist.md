# Development checklist

## Phase 1 — Repository & architecture ✅
- [x] Environment inspected (Python 3.13, lxml, Pillow, LibreOffice 24.2, Docker available; **no patent DOCX files provided**)
- [x] Project structure (`src/patent_converter`), pinned requirements, pyproject
- [x] Settings (YAML + env) with startup validation and business-rule enforcement
- [x] Claim-pattern profiles: validated, compiled at startup, selectable per request
- [x] API contract (success schema fixed, structured errors, status codes)
- [x] Canonical document model (`models/document_model.py`)
- [x] Supported-content matrix (`docs/supported_elements.md`)
- [x] Runnable skeleton: `/health`, `/ready`, `/metrics`, `/convert` (validation path), `/analyze`
- [x] Auth (hashed API keys), rate limit, job slots, body-size middleware, timeouts, workspaces, cleanup
- [x] Dockerfile / compose (prod + dev), `.env.example`; image built and verified

## Phase 2 — DOCX Capability Analyzer ✅ (on synthetic documents)
- [x] ZIP screening without decompression (bomb ratios, entries, sizes, traversal, symlinks, encryption, macros)
- [x] Secure XML (DTD/entity rejection, no network, depth guard)
- [x] Streaming `document.xml` scan; per-block release
- [x] All 20 required report items (see mapping below)
- [x] Claim detection with configurable patterns; numbering labels; SEQ fields; content controls
- [x] Header-only image probing (format by magic bytes, dimensions, frames, alpha, DPI)
- [x] Risk evaluation, HTML/JSON/NVARCHAR size estimate
- [x] JSON + human-readable reports; CLI and diagnostic API endpoint; no patent text by default
- [x] Bilingual number-based pairing comparison
- [x] 78 automated tests (unit, integration, API, security), passing locally and inside the image with pinned deps
- [x] Benchmarks: 10–100 MB × 500 claims; end-to-end 2 × 97 MB through uvicorn
- [ ] **Run on real patent documents** ← blocked: no files available

### Required report items → report fields
1 file size `file.sizeBytes` · 2 uncompressed `package.uncompressed_size` · 3 entries `package.entry_count` ·
4 paragraphs `structure.counts.paragraphs` · 5 tables `…tables/nested_tables` · 6 claim markers `claims.perClaim[].startBlock` ·
7 claim count `claims.count` · 8 images `media.count` · 9 formats `media.formats` · 10 image size `media.totalBytes` ·
11 dimensions `media.images[].width_px/height_px` · 12 DrawingML `…drawings/drawing_inline/drawing_anchor/drawing_groups` ·
13 VML `…vml_pict/vml_shapes/vml_imagedata` · 14 text boxes `…textboxes` · 15 OMML `…equations` + `ommlConstructs` ·
16 OLE `…ole_objects` + `oleProgIds` · 17 hyperlinks `…hyperlinks` · 18 unsupported XML `structure.unknownElements` ·
19 estimated HTML `estimates.*` · 20 risks `risks[]`

## Decisions needed from the product owner
1. **Real documents + production claim patterns.** Provide representative EN/AR patents (incl. chemical ones) so the analyzer can confirm patterns, numbering style and object types.
2. **Response size.** 2 × 100 MB image-heavy inputs ⇒ ≈ 270–280 MB JSON vs. the 100 MB limit (see performance.md).
3. **Tracked changes policy.** Reject, or render the "accepted" view (insertions kept, deletions removed)?
4. **Floating drawings.** Accept flow-layout approximation, or rasterize anchored content with its paragraph?
5. **OLE objects.** Confirm that the stored preview image is an acceptable rendering (it is the only safe option; objects are never executed).
6. **CKEditor version** used by OutSystems (4 vs 5 and its HTML/MathML filtering config) — determines equation output (MathML vs image).
7. **Synchronous 30-min requests** — confirm all timeouts on the OutSystems → LB → service path.

## Known limitations (current)
- Tests use synthetic documents written to mirror Word's OOXML; they prove structure handling, not coverage of every real-world authoring tool.
- Numbering resolution: counters per abstract numbering definition; unusual `w:lvlOverride` combinations may differ from Word (claims numbered this way are flagged `CLAIMS_AUTO_NUMBERED` for verification).
- Symbol-font characters are detected, not yet mapped to Unicode.
- Rate limiting and readiness are per uvicorn worker.
- Job isolation is thread + cooperative deadline; subprocess isolation with rlimits is planned for EMF/WMF conversion (Phase 4/6).
- `max_worker_memory_mb` is not enforced yet (Phase 6).
- Libxml2 rejects XML nesting deeper than 256 unless `xml_huge_tree` is enabled.

## Next: Phase 3 — Claim extraction engine
Reuse `ClaimBoundaryDetector` + block streaming; build canonical `Claim` objects
(paragraphs/runs/tables/drawings/equations/objects in order) per claim, writing
each finished claim to the workspace as it closes so memory stays bounded.
