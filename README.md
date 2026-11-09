# Patent Claims Converter

Bilingual (Arabic / English) patent-claims DOCX → HTML conversion platform for
OutSystems + CKEditor + SQL Server.

> **Status: Phase 1 (architecture, skeleton) and Phase 2 (DOCX Capability
> Analyzer) are complete.** Claim extraction into the canonical model,
> HTML rendering, image/equation conversion and response assembly (Phases 3–6)
> are **not implemented yet**. `POST /api/v1/patent/claims/convert` already runs
> the full intake, security screening, claim-boundary detection and
> number-based pairing validation, and returns `501 CONVERSION_NOT_IMPLEMENTED`
> when a pair passes all of them. See [docs/development_checklist.md](docs/development_checklist.md).

## What works today

| Capability | State |
|---|---|
| FastAPI service, `/health`, `/ready`, `/metrics` (Prometheus) | ✅ |
| API-key auth (SHA-256 hashed keys), rate limiting, per-worker job slots (429) | ✅ |
| Streaming disk-spooled uploads, size limits (413), signature checks (415) | ✅ |
| Per-request isolated workspaces with guaranteed cleanup, stale-job sweeping | ✅ |
| ZIP-bomb / path-traversal / symlink / macro / encrypted-entry screening | ✅ |
| Secure XML (DTD/entity rejection, no network, depth guard) | ✅ |
| Streaming `document.xml` scan (bounded memory) | ✅ |
| Configurable regex claim-boundary detection (EN/AR, explicit end or next-start mode) | ✅ |
| Word list-numbering resolution (auto-numbered claims), SEQ fields, content controls | ✅ |
| Number-based bilingual pairing validation with the required error contract | ✅ |
| DOCX Capability Analyzer (CLI + diagnostic API endpoint), JSON + text reports | ✅ |
| Canonical document model (definitions) | ✅ (populated from Phase 3) |
| Claim HTML rendering, Base64 embedding, OMML→MathML, EMF/WMF conversion | ❌ Phase 3–4 |
| Final `{"claims": [...]}` response assembly | ❌ Phase 5–6 |

## Quick start (development)

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                   # 78 tests
python scripts/generate_fixtures.py --out fixtures/  # synthetic bilingual DOCX set
python scripts/analyze_docx.py --en fixtures/patent_en.docx --ar fixtures/patent_ar.docx \
       --profile synthetic-test --out reports/
PATENT_CONFIG_FILE=config/app.dev.yaml PYTHONPATH=src \
       uvicorn patent_converter.main:create_app --factory --port 8000
```

**Run the analyzer on your real patent files first** (`--profile example`), then
create and verify a production pattern profile in `config/claim_patterns.yaml`.

## Docker

```bash
cp .env.example .env    # set PATENT__SECURITY__API_KEY_SHA256
docker compose up -d --build
curl localhost:8000/ready
```

Behind a TLS-inspecting proxy add `--secret id=pip_ca,src=/path/ca.pem` to `docker build`.

## Documentation

* [Architecture](docs/architecture.md) · [API](docs/api.md) · [Configuration](docs/configuration.md)
* [Supported elements matrix](docs/supported_elements.md) · [Performance & sizing](docs/performance.md)
* [Deployment](docs/deployment.md) · [Development checklist & limitations](docs/development_checklist.md)
* Analyzer reports for the synthetic fixtures: [docs/analysis/](docs/analysis/)

## Troubleshooting

| Symptom | Cause / action |
|---|---|
| Container exits: `production with auth_enabled requires ... api_key_sha256` | Set `PATENT__SECURITY__API_KEY_SHA256='["<sha256-hex>"]'`. |
| `CLAIMS_SECTION_NOT_FOUND` / `NO_CLAIMS_DETECTED` | Pattern profile does not match the document. Run `scripts/analyze_docx.py --samples` (confidential output!) and tune `config/claim_patterns.yaml`. |
| `CLAIMS_AUTO_NUMBERED` risk | Claim numbers come from Word numbering; verify the resolved numbers against Word. |
| `ORPHAN_CONTENT` | Content inside the claims section matched no claim (often an explicit end pattern firing early). |
| `INVALID_DOCX_STRUCTURE` with "Excessive depth" | XML nesting > 256; set `security.xml_huge_tree: true` only if a legitimate document needs it. |
| `429 CONCURRENCY_LIMIT` | Worker busy; `/ready` returns 503 meanwhile so the load balancer can route elsewhere. |
