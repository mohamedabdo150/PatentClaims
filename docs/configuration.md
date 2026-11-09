# Configuration

Settings load from `PATENT_CONFIG_FILE` (default `config/app.yaml`; development:
`config/app.dev.yaml`) and are overridden by environment variables
`PATENT__<SECTION>__<KEY>` (lists/dicts as JSON). Invalid configuration aborts
startup.

## Startup validation rules
* `output.embed_images_as_base64` must be `true`; `output.allow_external_images` must be `false` (business rules).
* `max_combined_upload_mb` ≥ each per-file limit.
* `environment: production` + `auth_enabled` requires ≥ 1 `api_key_sha256`.
* `stale_job_retention_minutes × 60` > `max_processing_seconds`.
* Every claim-pattern profile must compile; placeholders (`CONFIGURE_…`) are rejected;
  `claim_start_pattern` needs a `(?P<number>…)` group; `require_end_marker` requires `claim_end_pattern`.

## Settings reference (defaults = `config/app.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `processing.max_english_file_size_mb` / `max_arabic_file_size_mb` | 100 | per-file upload cap (413) |
| `processing.max_combined_upload_mb` | 200 | combined cap; body limit = this + 1 MB multipart overhead |
| `processing.max_claims_per_document` | 500 | more → `TOO_MANY_CLAIMS` |
| `processing.max_processing_seconds` | 1800 | job deadline (504) |
| `processing.max_concurrent_jobs_per_worker` | 1 | job slots per uvicorn worker (429 when full) |
| `processing.max_worker_memory_mb` | 40960 | not enforced in-process (measured peak ≈ 0.5 GB); enforce with the container memory limit (`MEMORY_LIMIT`) |
| `storage.temporary_directory` | `/data/patent-processing` | job workspaces (NVMe volume) |
| `storage.min_free_disk_gb` | 20 | below → `/ready` 503 and jobs rejected |
| `storage.stale_job_retention_minutes` | 120 | crash-recovery sweep age |
| `output.max_response_size_mb` | 100 | measured while the response is written; `OUTPUT_LIMIT_EXCEEDED` when exceeded. **See performance.md: 2×100 MB image-heavy pairs produce ≈ 270 MB** |
| `security.max_zip_entries` | 10000 | |
| `security.max_uncompressed_size_mb` | 1024 | total of central-directory sizes |
| `security.max_compression_ratio` | 150 | whole package (only when ≥ 1 MB uncompressed) |
| `security.max_entry_compression_ratio` | 500 | per entry ≥ `compression_ratio_min_entry_kb` |
| `security.max_document_xml_mb` | 512 | `document.xml` stream cap |
| `security.max_auxiliary_xml_mb` | 64 | rels / numbering / styles / content types |
| `security.max_image_pixels` | 200 000 000 | header-checked before decoding |
| `security.max_image_dimension_px` | 30000 | |
| `security.xml_huge_tree` | false | keep libxml2 depth/text-node guards |
| `security.reject_macro_documents` | true | VBA project / macro content types |
| `security.api_key_sha256` | `[]` | SHA-256 hex digests of accepted keys |
| `security.rate_limit_requests_per_minute` | 30 | per worker per client; 0 disables |
| `conversion.raster_dpi` | 192 | resolution of rasterized drawings, metafiles and equation images (2× CSS pixels) |
| `conversion.libreoffice_timeout_seconds` | 600 | per LibreOffice batch; the process tree is killed on timeout |
| `conversion.libreoffice_batch_size` | 100 | snippets per LibreOffice process |
| `conversion.prefer_svg` | true | embed Word's SVG (sanitized) instead of its PNG fallback |
| `conversion.max_asset_mb` | 200 | largest single media part read |
| `conversion.soffice_path` / `pdftocairo_path` | soffice / pdftocairo | external converters |
| `content.tracked_changes` | reject | `reject` → `UNSUPPORTED_CONTENT`; `accept` renders insertions and drops deletions (counted) |
| `content.hidden_text` | render | `render` (warning) or `drop` (counted as removed) |
| `content.equation_mode` | mathml | `mathml` (image fallback per unsupported equation) or `image` (all equations as images) |
| `content.preserve_font_family` / `preserve_font_size` | true | emit only when different from the document default |
| `analysis.markup_overhead_factor` | 2.5 | HTML size estimate heuristic |
| `analysis.include_text_samples` | false | CLI `--samples` only; never via API |

All numeric limits are initial targets to be tuned by benchmarking real documents.

## Claim pattern profiles (`config/claim_patterns.yaml`)

```yaml
profiles:
  <name>:
    verified: false            # set true only after validating on real documents
    english: &lang
      section_start_pattern: '…'    # optional; when null the whole body is the claims section
      section_end_pattern: '…'      # optional
      claim_start_pattern: '…(?P<number>…)…'
      claim_end_pattern: null       # null = claim ends at next start/section end; regex = explicit end marker
      case_sensitive: false
      match_mode: match             # match | search | fullmatch
      include_numbering_label: true # prepend resolved Word list label ("1.\t") before matching
      ignore_bidi_marks_for_matching: true
      max_match_chars: 2000
    arabic: { … }
    validation:
      require_section_start: true
      require_start_marker: true
      require_end_marker: false
      reject_duplicate_claim_numbers: true
      reject_unclosed_claims: true
      reject_orphan_content: true
      require_sequential_numbers: false
```

Shipped profiles: `example` (generic, **unverified**) and `synthetic-test`
(matches the test fixtures). Neither is a production profile. Select per request
with `configProfile`.

**Caution with explicit end markers:** an end regex such as `[.;]\s*$` closes a
claim at the first sentence-ending paragraph; intermediate paragraphs that end in
a period cause `ORPHAN_CONTENT`. Prefer `next_start` mode unless documents carry
a genuine end marker.
