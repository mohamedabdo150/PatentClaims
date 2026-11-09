# Legacy API (routes of the previous service)

The new service implements the routes of the previous service (`patent_claims`,
`AI_API_v2.py`) with **the same names, port (50013), request formats and response
keys**, so OutSystems needs no change. The `/api/v1/...` endpoints
([api.md](api.md)) are available in addition.

All responses are `application/json; charset=utf-8`. No API key is required, as before
(`legacy.require_api_key: false`); restrict port 50013 with the firewall (see
[windows_deployment.md](windows_deployment.md)).

## POST /word_to_json
`multipart/form-data` with `english_file` and/or `arabic_file` (at least one).

```json
{"claims": [{"id": 1, "claim_english": "<div …>…</div>", "claim_arabic": "<div dir=\"rtl\" …>…</div>"}]}
```
* `id` is the **claim number** (integer). Claims are paired by number, never by position.
* With only one file, the other language's key is omitted (as before).
* The HTML is the new high-fidelity rendering (images Base64-embedded, MathML, tables, RTL).

## POST /convert_word_to_html
Request body = the raw DOCX bytes. Converts the **whole document** (body; headers and
footers excluded): `{"success": true, "html": "<div class=\"pc-document\" dir=\"…\" lang=\"…\">…</div>"}`.
The language/direction is detected from the text.

## POST /drawing_detection
Request body = raw PDF. Same model file (`best_model.h5`) and preprocessing as before:
`{"results": [{"page": 1, "result": "Valid drawing" | "Not valid drawing"}]}`.

## POST /pdf-to-images
Request body = raw PDF. Every embedded image reference, page by page:
`{"success": true, "images": ["<base64>", …]}`.

## GET /health
`{"status":"OK","message":"Service is running smoothly.","extractor":"patent-converter","version":"…","model_loaded":true,"workers":3,"timeout":1800,"port":50013}`

## Errors
Error bodies contain the previous keys **and** the new structured fields:
```json
{"error": true, "message": "…", "detail": "…", "code": "CLAIM_PAIRING_MISMATCH", "requestId": "…", "details": {…}}
```
| Situation | Status | Previously |
|---|---|---|
| no file | 400, `detail` = "At least one file must be provided" | same |
| timeout | 504, `detail` = "Timeout after Ns" | same |
| unreadable PDF / model missing | 500, `detail` | same |
| claim extraction / pairing / unsupported content / output limit | 422 | v2 skipped files or returned partial results |

## Intentional differences from the previous service
| Previous behaviour | New behaviour | Why |
|---|---|---|
| Claims paired by position | Paired by claim number; `id` = claim number | patent requirement: never pair by position |
| A failing document was skipped and the other returned (with `message`) | Explicit error, no partial result | all-or-nothing requirement |
| EMF/WMF and drawing groups rejected (422) | Converted (LibreOffice rendering) | content support |
| `/convert_word_to_html` (v2) returned the joined claims | Whole document | confirmed requirement |
| Requests above the worker limit queued | Same: waits up to `processing.queue_wait_seconds` (600 s) | compatibility |
