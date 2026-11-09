# Architecture

## Goals that drive the design
1. Fidelity: patent wording is never translated, paraphrased, normalized or dropped.
2. Explicit failure: anything incomplete returns a structured error, never a partial 200.
3. Bounded memory regardless of input size (2 × 100 MB DOCX).
4. Self-contained output: Base64-embedded assets; no dependency on the DOCX after conversion.
5. Minimal infrastructure: one stateless service per node; LibreOffice and poppler as local, isolated subprocesses.

## Pipeline

```
 multipart ─▶ BodySizeLimit ─▶ auth / rate limit ─▶ JobLimiter (1 slot/worker) ─▶ job thread (Deadline)
   JobWorkspace /data/patent-processing/jobs/<rid>-<rand> (0700, always removed)
   1  upload_validator    stream to disk, per-file/combined caps, ZIP/CFB signature
   2  ANALYZER PASS       (per language)
        package_analyzer  central-directory screening: bombs, traversal, symlinks, macros, encryption
        iterparse document.xml → top-level blocks → block_scanner + ClaimBoundaryDetector
        image_probe       header-only formats/dimensions
   3  validate            rejections, claim boundary errors, number-based pairing   (422 on failure)
   4  EXTRACTION PASS     (per language) same block stream + same detector
        model_builder     → canonical model (Paragraph/Run/Table/Image/Equation/TextBox…)
                            + AssetRegistry (media parts and XML snippets)
        integrity         analyzer == extractor per claim: text chars, image refs, equations, tables
        content issues    unsupported content → 422 UNSUPPORTED_CONTENT
   5  ASSETS              one at a time → files in workspace
        raster validate / BMP,TIFF→PNG / SVG sanitize
        snippets (EMF/WMF, groups, canvases, VML, equation fallbacks) → minimal DOCX each →
        ONE LibreOffice process per batch (--convert-to pdf, killable, rlimits, no network env)
        → pdftocairo page 1 → crop using a calibration snippet (exact extent)
   6  RENDER per claim    model → HTML skeleton (placeholders) → sanitizer (must be a no-op)
                          → integrity (rendered text/images/equations/tables == model)
                          → stream to claims/NNNNN.html, expanding Base64 from asset files
   7  PAIR + ASSEMBLE     number-keyed pairs → response.json streamed with chunked JSON escaping,
                          size limit enforced while writing → moved to responses/ → FileResponse → deleted
```

Only one claim's HTML skeleton (no image bytes) is in memory at any time. Images
are read in 768 KB chunks while Base64-encoding, and the JSON is assembled
from disk in 512 K-character chunks.

## Module map (`src/patent_converter/`)

| Package | Responsibility |
|---|---|
| `api/` | routes, contract schemas, structured errors, auth, diagnostics store |
| `config/` | settings (YAML + env, startup validation), claim-pattern profiles |
| `security/` | upload persistence/validation, job slots, rate limit, deadlines, body-size middleware |
| `storage/` | per-request workspaces, stale job/response sweeping |
| `ooxml/` | namespaces, secure XML, package reader, relationships, numbering, styles, Symbol-font map |
| `analyzer/` | capability analyzer (screening, block scanner, image probe, risks, bilingual summary) |
| `extraction/` | boundary detector, claim-number keys, model builder, streaming claim extractor |
| `equations/` | OMML → MathML converter |
| `images/` | asset registry, asset pipeline, SVG sanitizer |
| `conversion/` | isolated subprocess runner, LibreOffice snippet renderer |
| `renderers/` | HTML renderer, Base64 streaming writer |
| `html/` | allowlist HTML sanitizer |
| `pairing/` | number-keyed pairing, streaming JSON response assembly |
| `validation/` | analyzer/extractor/renderer integrity checks |
| `pipeline/` | job orchestration, per-document converter |
| `models/` | canonical document model |
| `diagnostics/` | Prometheus metrics, JSON logging, resource sampling |

## Claim boundary detection

A deterministic state machine over top-level body blocks (content controls and
customXml are expanded):

```
BEFORE_SECTION ─section_start─▶ IN_SECTION ─claim_start─▶ IN_CLAIM
IN_CLAIM ─ claim_end (explicit mode) | next claim_start | section_end ─▶ …
```

Match text = resolved Word numbering label + logical paragraph text (text
boxes, deletions, field instructions and untaken AlternateContent excluded). Every
non-empty block in the section belongs to a claim or is reported as orphan content.

## Why two passes over the XML
The analyzer pass is cheap (< 0.3 s for 500 claims) and validates everything
that can fail *before* any expensive rendering. Its counters also serve as an
independent reference: the extractor must account for exactly the same text
characters, image references, equations and tables per claim, or the request
fails with `CONTENT_INTEGRITY_FAILED`. This turns "never silently drop content"
into a checked invariant rather than a code-review promise.

## Isolation of external converters
`conversion/subprocess_runner.py`: new session (whole process tree killed on
timeout), minimal environment (no proxy variables, private HOME/TMPDIR in the
workspace), `RLIMIT_CPU`, `RLIMIT_FSIZE` and no core dumps. Snippet DOCX files
contain only the fragment and the media it references. External links are
removed and macros/OLE binaries are never included or executed.

## Key decisions

| Decision | Reason |
|---|---|
| Raw lxml + zipfile instead of python-docx | python-docx hides drawings, OMML, VML and AlternateContent, and cannot stream |
| Reject DTD/ENTITY; keep libxml2 depth guard | XXE / billion-laughs / nesting bombs |
| LibreOffice snippet rendering with calibration | faithful drawings and metafiles with exact size; LibreOffice's inline offset is measured, not assumed |
| Own OMML → MathML, image fallback from OMML | structured, searchable equations; no partial MathML for unsupported constructs |
| Sanitizer must be a no-op on generated output | guarantees the sanitizer never strips legitimate content |
| Floating objects kept in flow (`pc-floating`) | agreed default; HTML has no page layout |
| Tracked changes rejected by default | no silent choice of document version |
| `/ready` = 503 while the slot is busy | load balancer routes the next job to the other node |
