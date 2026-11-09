# Architecture

## Goals that drive the design
1. Fidelity: patent wording is never translated, paraphrased, normalized or dropped.
2. Explicit failure: incomplete conversions return a structured error, never a partial 200.
3. Bounded memory regardless of input size (2 × 100 MB DOCX).
4. Self-contained output: Base64-embedded assets; no dependency on the DOCX after conversion.
5. Minimal infrastructure: one stateless Python service per node, no queues/blob stores.

## Component view

```
            ┌──────────────────────────── FastAPI worker (uvicorn) ───────────────────────────┐
 multipart  │ BodySizeLimitMiddleware → request-id middleware → auth/rate-limit → JobLimiter    │
 ─────────▶ │                                   │                                              │
            │                    ThreadPoolExecutor (job thread, Deadline)                     │
            │   ┌───────────────────────────────▼───────────────────────────────────────┐      │
            │   │ JobWorkspace (/data/patent-processing/jobs/<rid>-<rand>, 0700)        │      │
            │   │  1 upload_validator  : stream to disk, size caps, ZIP/CFB signature    │      │
            │   │  2 package_analyzer  : central-directory screening (no decompression)  │      │
            │   │  3 DocxPackage       : size-limited part streams, rels, content types  │      │
            │   │  4 NumberingResolver : numbering.xml + styles.xml (bounded)            │      │
            │   │  5 iterparse(document.xml) → top-level blocks → scan_block + detector  │      │
            │   │       (each block released immediately)                                │      │
            │   │  6 image_probe       : header-only, one image at a time                │      │
            │   │  7 capability_report : estimates + risks                               │      │
            │   │  8 pairing validation (by claim number)                                │      │
            │   │  ── Phase 3+: extractor → canonical model → renderer → disk → JSON ──  │      │
            │   └────────────────────────────────────────────────────────────────────────┘      │
            └───────────────────────────────────────────────────────────────────────────────────┘
```

## Module map (`src/patent_converter/`)

| Package | Responsibility |
|---|---|
| `api/` | routes, contract schemas, structured errors, auth & state |
| `config/` | settings (YAML + env), claim-pattern profiles (validated, compiled at startup) |
| `security/` | upload persistence/validation, job slots, rate limiting, deadlines, body-size middleware |
| `storage/` | per-request workspaces, stale-job sweeping, disk checks |
| `ooxml/` | namespaces, secure XML parsing, OPC package reader, relationships, list numbering |
| `analyzer/` | package screening, block scanner, image probe, DOCX analyzer, risk report, bilingual pair analysis |
| `extraction/` | claim-boundary state machine, claim-number normalization |
| `unicode_integrity/` | observe-only character diagnostics |
| `models/` | canonical document model (consumed from Phase 3) |
| `pipeline/` | job orchestration and conversion validation |
| `diagnostics/` | Prometheus metrics, JSON logging, resource sampling |

The proposed tree was adapted: everything lives in an importable `patent_converter`
package; modules for renderers/images/equations/html will be created when they
are implemented (no empty placeholder modules).

## Claim boundary detection

A deterministic state machine over **top-level body blocks** (paragraphs, tables;
content controls and customXml are expanded):

```
BEFORE_SECTION ─section_start─▶ IN_SECTION ─claim_start─▶ IN_CLAIM
IN_CLAIM ─ claim_end (explicit mode) | next claim_start | section_end ─▶ …
```

* Match text = Word numbering label (resolved from `w:numPr`) + logical paragraph text.
  Text boxes, deleted text, field instructions and untaken `mc:AlternateContent`
  branches are excluded; field results (e.g. `SEQ`) are included. Bidi marks are
  stripped from the *match copy only*.
* Every non-empty block inside the section belongs to a claim or is reported as
  `ORPHAN_CONTENT`. Duplicates, unclosed claims, missing sections, gaps and
  `max_claims_per_document` are validated.
* Numbers are normalized for **pairing keys only** (Arabic-Indic → ASCII); the raw
  number is preserved.

## Memory model

| Data | Strategy |
|---|---|
| Upload | Starlette spools to `TMPDIR` (set to the data volume), copied in 1 MB chunks to the workspace |
| ZIP | central directory only for screening; parts opened as size-limited streams |
| `document.xml` | `iterparse`; each top-level block is scanned then `clear()`ed and detached |
| numbering/styles/rels | parsed fully under `max_auxiliary_xml_mb` |
| images | header-only probe (`Image.open` lazy), never decoded in Phase 2 |
| **Phase 4–6 plan** | per-claim render → Base64 assets streamed into a per-claim HTML file on disk → final JSON streamed from disk, size measured before sending |

Measured: peak RSS ≈ 41 MB (analyzer) / 67 MB (server) for 2 × 97 MB inputs — see [performance.md](performance.md).

## Timeouts and isolation
Jobs run in a dedicated executor thread with a cooperative `Deadline` checked per
block and per image. On HTTP timeout the deadline is cancelled; the thread aborts
at the next checkpoint, removes its workspace and releases the job slot itself,
so a timed-out job can never overlap a new one. Phase 4 will run EMF/WMF
conversion in killable subprocesses with their own timeouts and rlimits.

## Key decisions

| Decision | Reason |
|---|---|
| Raw lxml + zipfile instead of python-docx for scanning | python-docx hides drawings, OMML, VML, AlternateContent and cannot stream |
| Reject any DTD/ENTITY | OOXML never needs them; eliminates XXE/billion-laughs |
| Keep libxml2 depth guard (`xml_huge_tree: false`) | rejects nesting bombs; real Word XML is far shallower than 256 |
| Patterns as validated config profiles | patterns change without code changes; broken regex fails at boot |
| `/ready` = 503 when the job slot is busy | lets the load balancer route the next large job to the other node |
| Errors never echo patent content or paths | confidentiality |
