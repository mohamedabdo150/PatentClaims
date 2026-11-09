# Development checklist

## Phase 1 — Repository & architecture ✅
Project structure, pinned dependencies, settings with startup validation,
claim-pattern profiles, API contract, canonical model, supported-elements matrix,
runnable skeleton, Docker.

## Phase 2 — DOCX Capability Analyzer ✅ (synthetic documents)
ZIP screening, secure XML, streaming scan, all 20 report items, claim detection
(patterns, numbering labels, SEQ fields, content controls), header-only image
probing, risks, size estimates, CLI + `/analyze`.

## Phase 3 — Claim extraction engine ✅
- [x] Style resolution (docDefaults, paragraph/character/table style chains)
- [x] Model builder: runs, paragraphs, hyperlinks, dir/bdo, tables (spans, borders, shading), images, text boxes, drawings, VML, OLE previews, equations, note references
- [x] Symbol-font → Unicode mapping
- [x] Streaming extractor sharing block stream and detector with the analyzer
- [x] Integrity: analyzer == extractor per claim (text chars, image refs, equations, tables)
- [x] Content issues classified as error / warning; tracked-changes policy

## Phase 4 — Rendering engine ✅
- [x] OMML → MathML converter with unsupported-construct detection
- [x] Isolated LibreOffice snippet renderer (batched, killable, rlimits, calibrated placement)
- [x] Asset pipeline: raster validation, BMP/TIFF → PNG, SVG sanitizer, EMF/WMF, groups, VML, equation fallbacks
- [x] HTML renderer (semantic, dir/lang, CSS allowlist) + Base64 streaming writer
- [x] Allowlist HTML sanitizer; generated output must pass unchanged
- [x] Rendered output integrity (text, images, equations, tables)

## Phase 5 — Bilingual pairing ✅
- [x] Number-keyed pairing, natural order, exact contract
- [x] Streaming JSON assembly with the size limit enforced while writing
- [x] FileResponse streaming; response deleted after delivery; stale sweeping
- [x] Diagnostics headers and endpoint; `strictValidation`

## Phase 6 — Large-document optimisation ✅ (sandbox)
- [x] Disk-backed processing end to end; one claim skeleton in memory
- [x] Benchmarks 10–100 MB × 500 claims per language through a real server and in the container
- [x] Memory measurement corrected to `VmHWM` (child `ru_maxrss` inherits the parent's peak)
- [x] CKEditor 4 / 5 round-trip compatibility measured
- [x] Visual review tooling (`scripts/visual_review.py`)

## Phase 7 — Docker & deployment 🟡
- [x] Ubuntu 24.04 image with LibreOffice/poppler/fonts, non-root, read-only, healthcheck, build-CA secret
- [x] Compose (prod + dev), `.env.example`, deployment and two-node guide
- [ ] Deploy on the two target nodes behind the load balancer

## Phase 8 — Integration & acceptance ⛔ (customer environment)
- [ ] Real Arabic/English patents: analyzer reports, verified pattern profile, regression fixtures
- [ ] OutSystems REST consume with a maximum-size response; SQL Server persistence; CKEditor in the application
- [ ] Decision on `max_response_size_mb`
- [ ] Benchmarks on 16 vCPU / 64 GB nodes; security review

## Known limitations
- All verification uses synthetic documents written to mirror Word's OOXML; real authoring tools (ChemDraw, MathType, older Word versions) may produce variants not yet seen.
- Numbering resolution keeps counters per abstract numbering definition; unusual `w:lvlOverride` combinations may differ from Word (flagged `CLAIMS_AUTO_NUMBERED`).
- Floating objects are placed in flow at their anchor; text wrapping and absolute page positions are not reproduced.
- Rasterized drawings (groups, shapes, VML, metafiles) become images: text inside them is not selectable in HTML (it is counted and reported as rasterized).
- Footnote/endnote text is outside the claim; only the reference mark is rendered (warning).
- Dingbat-font characters (Wingdings etc.) are kept with their font; browsers without that font show a substitute glyph (warning).
- LibreOffice rendering of OMML/metafiles can differ slightly from Word's rendering.
- Diagnostics endpoint, rate limiter and readiness are per worker process.
- `max_worker_memory_mb` is enforced through the container memory limit, not in-process.
