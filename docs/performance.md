# Performance and sizing

All numbers below come from **synthetic** documents on a 4 vCPU / 15 GB cloud
sandbox, not on the 16 vCPU / 64 GB target nodes. They show the scaling
behaviour, not final sizing. Real patents with many drawings need their own
benchmark (same scripts).

## Benchmark documents
`scripts/generate_fixtures.py --large 10 25 50 100 --claims 500`: per language,
500 claims, incompressible 1000×1000 PNG photos (worst case for Base64 size), an
EMF figure every 25 claims, a drawing group every 50 claims and an OMML equation
every 10 claims.

## End-to-end conversion (`scripts/benchmark_conversion.py`)
Real uvicorn server; peak memory = the **whole process tree** (Python and
LibreOffice children). Raw data: `benchmarks/conversion_limit100.json` and
`benchmarks/conversion_limit400.json`.

| Pair (EN + AR) | Input | `max_response_size_mb` | Result | Time | Response | Peak tree RSS |
|---|---:|---:|---|---:|---:|---:|
| large_10mb | 17.2 MB | 100 | 200, 1 000 claims, 66 images | 6.1 s | 24.1 MB | 284 MB |
| large_25mb | 45.8 MB | 100 | 200, 76 images | 7.2 s | 62.3 MB | 284 MB |
| large_50mb | 97.4 MB | 100 | **422 OUTPUT_LIMIT_EXCEEDED** | 6.9 s | — | 295 MB |
| large_100mb | 194.7 MB | 100 | **422 OUTPUT_LIMIT_EXCEEDED** | 7.4 s | — | 267 MB |
| large_50mb | 97.4 MB | 400 | 200, 94 images | 8.1 s | 131.0 MB | 273 MB |
| large_100mb | 194.7 MB | 400 | 200, 128 images | 9.5 s | 260.8 MB | 288 MB |

Production container (hardened, `WORKERS=1`, Docker image `patent-converter:1.0.0`),
2 × 97 MB with the limit at 400 MB: **200, 273 MB response, 10.9 s** including
upload and download, **peak container memory 486 MiB** (`docker stats`, includes
page cache).

Memory stays flat from 17 MB to 195 MB of input: images and HTML are streamed
to and from disk, and only one claim's HTML skeleton is held at a time.

## Analyzer only (`scripts/benchmark_analysis.py`)
Peak memory of a fresh process (`VmHWM`) ≈ 41 MB and < 0.3 s per 97 MB / 500-claim
document (`benchmarks/analyzer_synthetic.json`).

## Large documents with many drawings (EMF/WMF, groups, equations)
Figures that are not plain PNG/JPEG are rendered by LibreOffice. How that works:

* All snippets of a document are written into **a few batch documents**, one page
  per figure, plus a calibration page. Each batch costs one LibreOffice load and
  one `pdftocairo` run. The batches run in `conversion.libreoffice_parallelism`
  (default 4) LibreOffice processes at the same time. If a batch's page count
  doesn't match (a figure split across pages), only that batch is redone one
  figure per document, so the result is never wrong, only slower.
* Identical media at the same size are converted once and embedded at each occurrence.
* Rendered figures are stored as lossless PNG. Black-and-white/grey figures (most
  patent drawings) are stored as 8-bit greyscale: the same pixels at about a third
  of the size.
* For a pair, English and Arabic are converted at the same time when the server has
  at least 2 × `libreoffice_parallelism` CPUs (`processing.parallel_languages`,
  default automatic). On smaller machines that would only add memory.

Measured on this 4 vCPU sandbox:

| Document | Before | Now | HTML size before → now |
|---|---:|---:|---:|
| Customer document, 17 claims, 778 EMF figures (`/word_to_json`, one file) | 37 s | **13 s** | 12.6 MB → **6.1 MB** |
| Synthetic pair, 2 × (500 claims, 3 000 EMF, 250 groups, 250 equations) | — | **76 s**, peak tree RSS 0.95 GB | 19.7 MB response |

743 of the 779 figures are pixel-identical before and after; the other 36 differ only
in anti-aliasing of a few edge pixels.

Where the remaining time goes (3 000-figure document, summed over the 4
processes): LibreOffice ≈ 50 s, `pdftocairo` at 192 dpi ≈ 50 s, crop + PNG
encoding ≈ 30 s. Time scales with the number of **distinct** non-PNG figures
(≈ 12 ms each on these CPUs) and roughly inversely with the CPU count, up to
`libreoffice_parallelism`. On a 16 vCPU node, set `libreoffice_parallelism: 8`.

Other phases: analysis of 500 claims ≈ 1 s, extraction ≈ 1 s, rendering < 0.5 s,
Base64 + JSON assembly of 270 MB ≈ 1–2 s (disk bound).

If a large document still times out, check in this order:
1. `processing.max_processing_seconds` (job deadline, 504 on `/word_to_json`) and the
   OutSystems REST timeout, which must be longer than the conversion.
2. `conversion.libreoffice_timeout_seconds` (per batch).
3. `output.max_response_size_mb` (422 `OUTPUT_LIMIT_EXCEEDED`).
4. `GET /api/v1/patent/claims/diagnostics/{requestId}` for the per-phase timings.

## Output size (decision required)
Base64 adds 33 %, so the response is ≈ 1.34 × the embedded image bytes plus markup.
A 2 × 100 MB image-heavy pair produced a **261–273 MB** response, and
`NVARCHAR` (UTF-16) storage doubles that. The specified 100 MB limit therefore
rejects image-heavy pairs above ≈ 2 × 37 MB. The limit is configurable; raise it
only after the OutSystems path has been measured with a real maximum-size
response ([outsystems_integration.md](outsystems_integration.md)).

## Sizing guidance for the target nodes (to be validated)
* One conversion per worker (`max_concurrent_jobs_per_worker: 1`). Measured
  peak ≈ 0.3–0.5 GB per job here, so memory is not the constraint on 64 GB nodes.
  CPU (LibreOffice) and response size are.
* `WORKERS` = 2–4 per node allows parallel conversions. `/ready` returns 503
  while a worker is busy, so the load balancer spreads jobs across nodes.
* Temporary storage per job ≈ 2 × input + 2 × response (workspace + response
  file); keep `min_free_disk_gb` above `WORKERS` × that.
