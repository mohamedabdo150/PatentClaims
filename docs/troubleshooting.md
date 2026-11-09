# Troubleshooting

| Symptom | Cause / action |
|---|---|
| Container exits: `production with auth_enabled requires ... api_key_sha256` | Set `PATENT__SECURITY__API_KEY_SHA256='["<sha256-hex>"]'`. |
| `CLAIM_EXTRACTION_FAILED` + `CLAIMS_SECTION_NOT_FOUND` / `NO_CLAIMS_DETECTED` | The pattern profile does not match the document. Run `scripts/analyze_docx.py --samples` (output contains patent text, so treat it as confidential) and tune `config/claim_patterns.yaml`. |
| `MISSING_CLAIM_NUMBER` | A `{start}`/`{next}` block contains no claim number. Typical for PDF conversions: the number paragraph sits after the `{next}` marker. Move the marker after the number, or add the number. `details` gives the block index. |
| `AMBIGUOUS_CLAIM_NUMBER` | Two different numbers in one claim block: usually a missing `{next}` between two claims, or a displaced number from the previous claim. |
| `AMBIGUOUS_MARKER` | `{next}` in the middle of a paragraph; split the paragraph so the marker is at its start or end. |
| `ORPHAN_CONTENT` | Content inside the claims section matched no claim, often because an explicit end pattern fired early. Prefer `claim_end_pattern: null` (next-start mode). |
| `UNCLOSED_CLAIM` | Explicit-end mode and a claim's last paragraph does not match the end pattern. |
| `CLAIM_PAIRING_MISMATCH` | The claim number sets differ. `details` lists the missing/duplicate numbers per language. |
| `UNSUPPORTED_CONTENT` / `TRACKED_CHANGES` | Accept or reject revisions in Word, or set `content.tracked_changes: accept` to render the accepted view. |
| `UNSUPPORTED_CONTENT` / `LINKED_IMAGE` | The picture is linked to an external file. Embed it in Word (*File → Info → Edit links → Save picture in document*). |
| `UNSUPPORTED_CONTENT` / `CHART`, `SMARTART` | Convert the chart or diagram to a picture in Word. |
| `UNSUPPORTED_CONTENT` / `OLE_WITHOUT_PREVIEW` | Open the object in Word once so it stores a preview, or replace it with a picture. |
| `ASSET_CONVERSION_FAILED` / `EMPTY_RENDER` | LibreOffice produced a blank image for a metafile or drawing; check the figure in Word. |
| `ASSET_CONVERSION_FAILED` / `LIBREOFFICE_TIMEOUT` | Raise `conversion.libreoffice_timeout_seconds` or lower `libreoffice_batch_size`. |
| `ASSET_CONVERSION_FAILED` / `CALIBRATION_FAILED` | LibreOffice placement could not be measured; check the LibreOffice installation (`soffice --version`). |
| `CONTENT_INTEGRITY_FAILED` (500) | Converter defect: the analyzer, extractor or renderer disagree on content. Report it with the `requestId` and `details` (no patent text is included). |
| `OUTPUT_LIMIT_EXCEEDED` | The complete response is larger than `output.max_response_size_mb`. See [performance.md](performance.md#output-size-decision-required). |
| Subscripts/superscripts or Arabic direction lost in CKEditor | CKEditor filter configuration; see [ckeditor_integration.md](ckeditor_integration.md). |
| Equations shown as plain text in CKEditor | MathML filtered by CKEditor. Allow it, or use `content.equation_mode: image`. |
| `INVALID_DOCX` with "Excessive depth" | XML nesting > 256. Set `security.xml_huge_tree: true` only if a legitimate document needs it. |
| `429 CONCURRENCY_LIMIT` | Worker busy; `/ready` is 503 meanwhile so the load balancer can route elsewhere. |
| `CLAIMS_AUTO_NUMBERED` risk in analyzer reports | Claim numbers come from Word numbering; verify the resolved numbers against Word. |
