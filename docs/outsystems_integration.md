# OutSystems integration

> **Existing OutSystems integration:** keep calling the previous routes
> (`/word_to_json`, `/convert_word_to_html`, `/drawing_detection`, `/pdf-to-images`) on port
> 50013. They are implemented by the new service with the same formats
> ([legacy_api.md](legacy_api.md)). The `/api/v1` contract below is for new integrations.

## Flow

```
User uploads EN + AR DOCX ──▶ OutSystems server action ──▶ POST /api/v1/patent/claims/convert
                                                              │ (multipart, X-API-Key)
                                  ◀── 200 {"claims":[…]} ─────┘   or structured error
OutSystems deserializes claims[] ──▶ persists ClaimNumber / ClaimAr / ClaimEn ──▶ CKEditor
```

## REST consume configuration

| Item | Value |
|---|---|
| Method / URL | `POST https://<lb-host>/api/v1/patent/claims/convert` |
| Body | `multipart/form-data`: `englishFile` (binary), `arabicFile` (binary); optional text fields `configProfile`, `requestId`, `strictValidation` (`true`/`false`), `allowPartialResult` (must be `false`) |
| Headers | `X-API-Key: <key>` (the service stores only its SHA-256); optional `X-Request-ID` |
| Timeout | ≥ `processing.max_processing_seconds` + upload/download time. Synthetic 2 × 97 MB took ~11 s; real documents with many drawings will take longer. Measure and set accordingly |
| Response | `application/json`, exactly `{"claims":[{"claimNumber":"…","claimAr":"…","claimEn":"…"}]}` |

Multipart: OutSystems REST consume supports binary multipart bodies through the
`multipart/form-data` request format, or through a small extension that builds
the body. Field names are case-sensitive.

### Response structures

```
ConvertResponse
  claims : List of ClaimPair
ClaimPair
  claimNumber : Text
  claimAr     : Text
  claimEn     : Text
ErrorResponse
  error : ErrorDetail { code : Text, message : Text, requestId : Text, details : (JSON text/object) }
```

`details` has a code-specific shape (see [api.md](api.md)). Map it as text, or
handle it in `OnAfterResponse`.

### Error handling

* Treat **only HTTP 200** as success; any other status carries `{"error": …}`.
* Retry only `429` (honour `Retry-After`), `503` and `504`. `4xx` validation
  errors (`CLAIM_PAIRING_MISMATCH`, `UNSUPPORTED_CONTENT`, …) need user action.
* Log `X-Request-ID`. `GET /api/v1/patent/claims/diagnostics/{requestId}` returns
  counts, warnings and timings for that conversion (no patent content; kept in
  memory on the worker that served it).

Diagnostic response headers on success: `X-Claims-Count`, `X-Response-Bytes`,
`X-Processing-Seconds`, `X-Images-Embedded`, `X-Equations-Rendered`,
`X-Tables-Rendered`, `X-Warnings-Count`.

## Large responses — must be measured (Phase 8)

* Base64 makes HTML ≈ 1.34 × the image bytes. A 2 × 100 MB image-heavy pair
  produced **273 MB** of JSON in the synthetic benchmark. The default
  `output.max_response_size_mb: 100` rejects such pairs with
  `OUTPUT_LIMIT_EXCEEDED` (no truncation). Raising the limit is a deliberate
  decision once the OutSystems path is proven.
* Verify with a maximum-size response:
  * the platform's limits on REST response size and deserialization memory,
  * the integration timeout,
  * any reverse proxy or load balancer between OutSystems and the service
    (buffering, body size, idle timeout),
  * CKEditor load time for the largest single claim.
* OutSystems Cloud / ODC impose platform limits that a 100+ MB payload may
  exceed. If so, the contract needs an approved alternative, such as per-claim
  retrieval or asynchronous delivery. That is not implemented without approval.

## SQL Server persistence

Reference schema (adapt to the existing model):

```sql
CREATE TABLE PatentClaims (
    Id          BIGINT IDENTITY(1,1) PRIMARY KEY,
    PatentId    BIGINT        NOT NULL,
    ClaimNumber NVARCHAR(50)  NOT NULL,
    ClaimAr     NVARCHAR(MAX) NOT NULL,
    ClaimEn     NVARCHAR(MAX) NOT NULL,
    CONSTRAINT UQ_PatentClaims UNIQUE (PatentId, ClaimNumber)
);
```

* `NVARCHAR` stores UTF-16: Base64 and markup cost 2 bytes per character, so a
  claim's storage ≈ 2 × its HTML size. `NVARCHAR(MAX)` holds up to 2 GB per
  value, but the whole path (OutSystems entity attribute type *Text* with a
  large length, data-access layer, transaction size) must be checked for
  truncation with the largest real claim.
* Persist all claims of a patent in **one transaction**. A failed conversion
  stores nothing, consistent with the service's all-or-nothing contract.
* Never sort by array position on read: order by a natural sort of
  `ClaimNumber` (e.g. a persisted numeric sort key).
