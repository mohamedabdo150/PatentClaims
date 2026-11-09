# Patent Claims Converter

Bilingual (Arabic / English) patent-claims DOCX → HTML conversion service for
OutSystems + CKEditor + SQL Server.

`POST /api/v1/patent/claims/convert` takes an English and an Arabic DOCX,
extracts the claims of each document independently with configurable patterns,
renders every claim to self-contained HTML (all images Base64-embedded), pairs
the claims **by claim number** and returns one complete response:

```json
{"claims": [{"claimNumber": "1", "claimAr": "<div dir=\"rtl\" …>", "claimEn": "<div dir=\"ltr\" …>"}]}
```

Any condition that would make the result incomplete (unsupported content, image
conversion failure, pairing mismatch, output limit, timeout) returns a structured
error instead — never a partial `200`.

> **Status:** Phases 1–6 implemented. Verified on synthetic documents **and on
> the customer sample set** (4 English claims documents converted completely,
> including 778 EMF chemical structures; the Arabic sample is rejected for
> documented source defects); see [docs/real_document_results.md](docs/real_document_results.md).
> Phase 7 (deployment) is implemented, but not on the target nodes. Phase 8
> (OutSystems, SQL Server and production acceptance) needs your environment.
> See [docs/production_readiness.md](docs/production_readiness.md).

## Highlights

| Area | What is implemented |
|---|---|
| Extraction | Streaming `document.xml` pass, configurable claim boundaries: `{start}`/`{next}` markers (default `marker` profile, including markers inside PDF layout tables) or start/end regexes; Word list numbering, SEQ fields, content controls |
| Fidelity | Exact text (no translation/normalization), Symbol-font → Unicode, RTL via `dir`/`lang`, run formatting, tables with row/col spans and borders, text boxes, hyperlinks |
| Images | PNG/JPEG/GIF/WebP as-is, BMP/TIFF → PNG, sanitized SVG, EMF/WMF + drawing groups/canvases/VML shapes rendered by isolated LibreOffice with exact placement |
| Equations | Own OMML → MathML converter; equations outside its coverage are rendered from the original OMML to images; `equation_mode: image` switch |
| Integrity | Independent analyzer and extractor passes must agree exactly per claim (text characters, image references, equations, tables); rendered HTML must contain all of it; sanitizer must not change generated HTML |
| Security | ZIP-bomb/traversal/macro screening, DTD/entity rejection, allowlist HTML + SVG sanitizers, no network access during conversion, non-root read-only container |
| Scale | 2 × 97 MB, 1 000 claims, 128 images, 273 MB response: **~11 s, < 0.5 GiB** container memory (synthetic) |

## Previous-service routes (no OutSystems change)

The routes of the previous service (`patent_claims`, `AI_API_v2.py`) are implemented with the
same names, port **50013**, request formats and response keys: `POST /word_to_json`,
`POST /convert_word_to_html`, `POST /drawing_detection`, `POST /pdf-to-images`,
`GET /health`. See [docs/legacy_api.md](docs/legacy_api.md).

## Windows Server

Install natively as a Windows service (NSSM), not Docker:
[docs/windows_deployment.md](docs/windows_deployment.md).

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -AllowedClients <OutSystems IPs>
```

## Quick start (development)

Requirements: Python 3.12+, LibreOffice (writer, draw, math), poppler-utils.

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                    # 124 tests (performance: -m performance)
PATENT_SAMPLES_DIR=/path/to/samples python -m pytest tests/regression   # customer samples (not in repo)
python scripts/generate_fixtures.py --out fixtures/   # synthetic bilingual DOCX set
PATENT_CONFIG_FILE=config/app.dev.yaml PYTHONPATH=src \
  uvicorn patent_converter.main:create_app --factory --port 8000
curl -F englishFile=@fixtures/patent_en.docx -F arabicFile=@fixtures/patent_ar.docx \
     -F configProfile=synthetic-test localhost:8000/api/v1/patent/claims/convert
```

The default profile `marker` matches the customer documents (`{start}` / `{next}`).
To check new document sources, run the analyzer:

```bash
python scripts/analyze_docx.py --en real_en.docx --ar real_ar.docx --profile marker --out reports/
```

## Docker

```bash
cp .env.example .env    # set PATENT__SECURITY__API_KEY_SHA256
docker compose up -d --build
curl localhost:8000/ready
```

Behind a TLS-inspecting proxy, add `--secret id=build_ca,src=/path/ca.pem` to `docker build`.

## CKEditor: configuration is mandatory

With CKEditor's **default** content filter, `<sub>`/`<sup>` (C₆H₆ → C6H6),
`dir` attributes and MathML are stripped. That was verified against CKEditor 4.22.1
and 5 (47.4). Use the configuration in [docs/ckeditor_integration.md](docs/ckeditor_integration.md).

## Documentation

* [Legacy API (previous service)](docs/legacy_api.md) · [Windows deployment](docs/windows_deployment.md)
* [Architecture](docs/architecture.md) · [API](docs/api.md) · [Configuration](docs/configuration.md)
* [Supported elements](docs/supported_elements.md) · [Performance](docs/performance.md) · [Deployment](docs/deployment.md)
* [OutSystems integration](docs/outsystems_integration.md) · [CKEditor integration](docs/ckeditor_integration.md)
* [Troubleshooting](docs/troubleshooting.md) · [Production readiness](docs/production_readiness.md)
* [Development checklist & limitations](docs/development_checklist.md)

## Tooling

| Script | Purpose |
|---|---|
| `scripts/analyze_docx.py` | DOCX Capability Analyzer (JSON + text reports) |
| `scripts/generate_fixtures.py` | synthetic fixtures, incl. large benchmark documents |
| `scripts/benchmark_analysis.py` | analyzer time / memory per document |
| `scripts/benchmark_conversion.py` | end-to-end conversion through a real server (time, peak memory of the process tree, response size) |
| `scripts/visual_review.py` | side-by-side review sheet: source DOCX (LibreOffice) vs converted HTML (Chromium) |
| `tests/ckeditor/*.js` | CKEditor 4 / 5 round-trip compatibility checks (Node + Playwright) |
