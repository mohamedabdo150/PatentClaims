# API

All error responses use:
```json
{"error": {"code": "…", "message": "…", "requestId": "…", "details": {}}}
```
Every response carries `X-Request-ID` (from the `requestId` form field, the
`X-Request-ID` header, or generated). Authentication: `X-API-Key` header
(configurable name); keys are stored only as SHA-256 digests.

## POST /api/v1/patent/claims/convert
`multipart/form-data`

| Field | Required | Notes |
|---|---|---|
| `englishFile` | yes | `.docx`, ≤ `max_english_file_size_mb` |
| `arabicFile` | yes | `.docx`, ≤ `max_arabic_file_size_mb` |
| `configProfile` | no | claim pattern profile (default `default_config_profile`) |
| `requestId` | no | `[A-Za-z0-9._-]{1,64}` |
| `strictValidation` | no | accepted; strict is the only mode in this phase |
| `allowPartialResult` | no | default `false`; `true` → `400 PARTIAL_RESULT_NOT_ENABLED` |

**Success contract (Phase 5+, fixed):**
```json
{"claims": [{"claimNumber": "1", "claimAr": "<div dir=\"rtl\" …>", "claimEn": "<div dir=\"ltr\" …>"}]}
```
**Current behaviour:** after successful validation and pairing →
`501 CONVERSION_NOT_IMPLEMENTED` with `details.claimCount`.

## POST /api/v1/patent/claims/analyze (diagnostic)
Same file fields plus `configProfile`, `requestId`. Returns `200` with the
capability reports for both documents, the number-based pairing comparison and
the combined output-size estimate. **No patent text is returned** (text samples
are a CLI-only option).

## GET /health · GET /ready · GET /metrics
* `/health` – liveness (process up).
* `/ready` – storage writable, free space ≥ `min_free_disk_gb`, a job slot free → `200`, else `503`.
* `/metrics` – Prometheus text format (`patent_*` series).

## Error codes

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_REQUEST`, `MISSING_FILE`, `UNKNOWN_CONFIG_PROFILE`, `PARTIAL_RESULT_NOT_ENABLED` | malformed request |
| 401 | `UNAUTHORIZED` | missing/invalid API key |
| 413 | `UPLOAD_TOO_LARGE`, `FILE_TOO_LARGE` | body/combined/per-file limit |
| 415 | `UNSUPPORTED_FILE_TYPE`, `ENCRYPTED_OR_LEGACY_DOCUMENT` | not a DOCX ZIP; password-protected or `.doc` |
| 422 | `INVALID_DOCX` | broken package / missing main part |
| 422 | `DOCUMENT_REJECTED` | security screening (ZIP bomb, macros, unsafe XML, traversal…) – `details.rejected` lists finding codes |
| 422 | `CLAIM_EXTRACTION_FAILED` | boundary errors – `details.issues.{english,arabic}[]` |
| 422 | `CLAIM_PAIRING_MISMATCH` | number sets differ – `missingArabicClaims`, `missingEnglishClaims`, `duplicateArabicClaims`, `duplicateEnglishClaims` |
| 422 | `OUTPUT_LIMIT_EXCEEDED` | (Phase 6) measured response > `max_response_size_mb` |
| 429 | `RATE_LIMITED`, `CONCURRENCY_LIMIT` | with `Retry-After` |
| 500 | `INTERNAL_ERROR` | unexpected; no internals exposed |
| 501 | `CONVERSION_NOT_IMPLEMENTED` | current phase only |
| 503 | `INSUFFICIENT_STORAGE`, `SERVICE_UNAVAILABLE` | |
| 504 | `PROCESSING_TIMEOUT` | `max_processing_seconds` exceeded |

Interactive OpenAPI docs: `/docs` (disabled when `environment: production`).
