# Performance and sizing

## Phase 2 measurements (synthetic documents)

Environment: 4 vCPU / 15 GB cloud sandbox, Python 3.13, lxml 6.1. Documents: 500
claims each, incompressible 1000×1000 PNG images (worst case for size), OMML
every 10 claims. Raw data: [benchmarks/analyzer_synthetic.json](benchmarks/analyzer_synthetic.json).

| File | DOCX MB | Claims | Images | Analyze s | Peak RSS | Est. claims HTML |
|---|---:|---:|---:|---:|---:|---:|
| large_10mb_en | 8.6 | 500 | 3 | 0.11 | 41 MB | 12.2 MB |
| large_25mb_en | 22.9 | 500 | 8 | 0.13 | 41 MB | 31.3 MB |
| large_50mb_en | 48.7 | 500 | 17 | 0.17 | 41 MB | 65.6 MB |
| large_100mb_en | 97.4 | 500 | 34 | 0.19 | 41 MB | 130.5 MB |
| large_100mb_ar | 97.4 | 500 | 34 | 0.27 | 41 MB | 130.8 MB |

End-to-end through uvicorn (`/analyze` and `/convert` validation path) with a
2 × 97.4 MB multipart upload (204 MB body): **1.1–1.2 s total, server peak RSS
67 MB**, workspace and spool files removed afterwards.

**What this proves:** intake, screening, streaming XML scan and header-only image
probing are memory-bounded and fast. **What it does not prove:** conversion cost.
Rendering, image conversion (EMF/WMF/TIFF), Base64 encoding and JSON assembly
(Phases 4–6) will dominate time and memory and must be benchmarked on real
patents before sizing is confirmed.

## Output-size finding (action required)

Base64 adds 33 %; image-heavy DOCX produce HTML ≈ 1.34 × the DOCX size
(more when EMF/WMF/TIFF are converted to PNG). For 2 × 100 MB inputs the
estimated JSON response is **≈ 270–280 MB**, which:

* exceeds the specified `output.max_response_size_mb: 100`;
* must pass through OutSystems REST deserialization (platform limits and memory)
  and gateway buffering;
* becomes ≈ 2× in SQL Server `NVARCHAR(MAX)` (UTF-16) – ~550 MB across rows.

Options to decide before Phase 6 (contract changes need approval):
1. Raise `max_response_size_mb` after measuring the OutSystems path with a real
   maximum-size response.
2. Keep the contract but reduce embedded size without degrading drawings
   (lossless PNG optimisation, de-duplicate repeated images *within a claim*).
3. Approve an alternative delivery (e.g. per-claim retrieval) – not implemented
   without approval.

The analyzer reports `ESTIMATED_RESPONSE_EXCEEDS_LIMIT` per document and a
combined estimate per pair so real patents can be assessed now.

## Synchronous 30-minute requests
Load balancers, reverse proxies and OutSystems REST integrations typically have
timeouts far below 30 minutes. All timeouts on the path (OutSystems
integration timeout, LB idle timeout, uvicorn keep-alive) must be configured and
verified in Phase 8.
