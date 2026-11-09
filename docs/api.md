# API

All error responses use:
```json
{"error": {"code": "…", "message": "…", "requestId": "…", "details": {}}}
```
Every response carries `X-Request-ID` (from the `requestId` form field, the
`X-Request-ID` header, or generated). Authentication: `X-API-Key` header
(configurable name). Keys are stored only as SHA-256 digests. Public errors never
contain stack traces, file paths or patent content.

## POST /api/v1/patent/claims/convert
`multipart/form-data`

| Field | Required | Notes |
|---|---|---|
| `englishFile` | yes | `.docx`, ≤ `max_english_file_size_mb` |
| `arabicFile` | yes | `.docx`, ≤ `max_arabic_file_size_mb` |
| `configProfile` | no | claim pattern profile (default `default_config_profile`) |
| `requestId` | no | `[A-Za-z0-9._-]{1,64}` |
| `strictValidation` | no | `true` turns documented warnings into errors: unmapped symbol characters, dingbat fonts, footnote/endnote content outside the claim, hidden text, dropped hyperlink schemes, ignored empty elements, equations rendered as images |
| `allowPartialResult` | no | default `false`; `true` → `400 PARTIAL_RESULT_NOT_ENABLED` (conversion is all-or-nothing) |

**Success (200)**, exactly:
```json
{"claims": [{"claimNumber": "1", "claimAr": "<div class=\"pc-claim\" dir=\"rtl\" lang=\"ar\">…</div>",
             "claimEn": "<div class=\"pc-claim\" dir=\"ltr\" lang=\"en\">…</div>"}]}
```
Claims are paired by normalized claim number (Arabic-Indic digits → ASCII for the
key; the HTML keeps the original digits) and ordered by natural number sort.
The body is streamed from disk. Headers: `X-Claims-Count`, `X-Response-Bytes`,
`X-Processing-Seconds`, `X-Images-Embedded`, `X-Equations-Rendered`,
`X-Tables-Rendered`, `X-Warnings-Count`.

## GET /api/v1/patent/claims/diagnostics/{requestId}
Diagnostics of a recent successful conversion on the worker that served it (in
memory, last 200). Per language: claims detected/extracted/rendered, image
references detected vs images embedded, equations (MathML / image), tables,
rasterized drawings, warnings by code, Unicode warnings, HTML bytes, asset
statistics and timings, plus analyzer risks. No patent text.
`404 NOT_FOUND` when unknown.

## POST /api/v1/patent/claims/analyze (diagnostic)
Same file fields plus `configProfile` and `requestId`. Returns `200` with the
capability reports of both documents, the number-based pairing comparison and the
output-size estimate. No patent text.

## GET /health · GET /ready · GET /metrics
* `/health`: liveness.
* `/ready`: storage writable, free space ≥ `min_free_disk_gb`, and a job slot free → `200`; otherwise `503`, so a load balancer routes large jobs to the other node.
* `/metrics`: Prometheus (`patent_*`: requests, failures by code, durations by phase, input/output sizes, claims, images, equations, risk findings, active jobs, last job peak RSS/CPU, temp storage).

## Error codes

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_REQUEST`, `MISSING_FILE`, `UNKNOWN_CONFIG_PROFILE`, `PARTIAL_RESULT_NOT_ENABLED` | malformed request |
| 401 | `UNAUTHORIZED` | missing/invalid API key |
| 404 | `NOT_FOUND` | unknown diagnostics id |
| 413 | `UPLOAD_TOO_LARGE`, `FILE_TOO_LARGE` | body / combined / per-file limit |
| 415 | `UNSUPPORTED_FILE_TYPE`, `ENCRYPTED_OR_LEGACY_DOCUMENT` | not a DOCX ZIP; password-protected or `.doc` |
| 422 | `INVALID_DOCX` | broken package / missing main part |
| 422 | `DOCUMENT_REJECTED` | security screening (ZIP bomb, macros, unsafe XML, traversal, …); `details.rejected` |
| 422 | `CLAIM_EXTRACTION_FAILED` | boundary errors (missing section, unclosed claim, duplicates, orphan content, too many claims); `details.issues.{english,arabic}[]` |
| 422 | `CLAIM_PAIRING_MISMATCH` | `missingArabicClaims`, `missingEnglishClaims`, `duplicateArabicClaims`, `duplicateEnglishClaims` |
| 422 | `UNSUPPORTED_CONTENT` | content that cannot be rendered faithfully: tracked changes (policy `reject`), linked external images, charts, SmartArt, altChunk, OLE without preview, ActiveX, unknown elements with content; `details.{english,arabic}[] = {claimNumber, code, message, count}` |
| 422 | `ASSET_CONVERSION_FAILED` | an image could not be validated/converted (`details.{lang}[] = {claimNumber, asset, code}`) |
| 422 | `OUTPUT_LIMIT_EXCEEDED` | measured response > `output.max_response_size_mb` (never truncated) |
| 429 | `RATE_LIMITED`, `CONCURRENCY_LIMIT` | with `Retry-After` |
| 500 | `CONTENT_INTEGRITY_FAILED` | internal cross-check failed (analyzer vs extractor vs rendered HTML, or sanitizer would alter output). Indicates a converter defect; the request is safe to report |
| 500 | `INTERNAL_ERROR` | unexpected |
| 503 | `INSUFFICIENT_STORAGE`, `SERVICE_UNAVAILABLE` | |
| 504 | `PROCESSING_TIMEOUT` | `max_processing_seconds` exceeded |

Interactive OpenAPI: `/docs` (disabled when `environment: production`).
