# Deployment

> **Windows Server:** install natively as a Windows service; see
> [windows_deployment.md](windows_deployment.md). This page covers the Linux/Docker option.

## Image (`Dockerfile`)
* Base `ubuntu:24.04` (Ubuntu Server LTS, as specified), Python 3.12, multi-stage,
  pinned `requirements.txt`.
* Runtime packages: `libreoffice-{writer,draw,math}-nogui` (no GUI stack),
  `poppler-utils`, fonts for Latin/Greek/math/Arabic (DejaVu, Liberation, Carlito,
  Caladea, Noto core incl. Naskh Arabic, Amiri, STIX). Image ≈ 940 MB, mostly LibreOffice.
* Non-root user (uid 10001). Read-only root filesystem supported. Writable
  volume `/data/patent-processing` holds `jobs/`, `responses/` and the upload
  spool (`TMPDIR`).
* `HEALTHCHECK` → `/health`. Startup validates configuration and claim
  patterns, and refuses to start in production without an API key hash.
* Optional build secret for TLS-inspecting proxies, used by apt and pip
  during the build only: `docker build --secret id=build_ca,src=/path/ca.pem .`

Verified: image builds; production refuses to boot without an API key;
hardened run (`--read-only --cap-drop ALL --security-opt no-new-privileges`)
reports healthy; a 2 × 97 MB pair converts end to end; no job files, responses or
LibreOffice processes remain afterwards; 120/120 tests pass inside the image on
the pinned dependencies.

## Run
```bash
cp .env.example .env            # set PATENT__SECURITY__API_KEY_SHA256 (sha256 of the key)
docker compose up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build   # development
```

## Two-node layout

```
          OutSystems ──HTTPS──▶ Load balancer (TLS termination, health check GET /ready)
                                   │                          │
                    node A: docker compose            node B: docker compose
                    WORKERS × 1 job slot              WORKERS × 1 job slot
                    /mnt/nvme → /data/…               /mnt/nvme → /data/…
```

* Bind the volume to local NVMe on each node (see the `volumes` comment in
  `docker-compose.yml`).
* `/ready` returns 503 while a worker's job slot is busy, so the next job goes to
  the other node. With `WORKERS > 1`, readiness reflects the worker that answered
  the probe.
* Load balancer / reverse proxy must allow request bodies ≥ 201 MB, must not
  buffer large responses in memory (or must be sized for them), and needs an
  idle/read timeout ≥ `processing.max_processing_seconds` + transfer time.
* Rate limiting is per worker; also enforce it at the load balancer.
* Logs are JSON on stdout (request id, timings, counts; never patent content).
  Scrape `/metrics` with Prometheus.

## Operations
* Crash recovery: workspaces and undelivered responses older than
  `stale_job_retention_minutes` are swept at startup and every 10 minutes.
* A timed-out job is cancelled cooperatively. Its LibreOffice batch is killed with
  the whole process tree, and the job slot is released only when the worker
  thread has finished and cleaned up.
* Upgrade LibreOffice deliberately. Snippet placement is calibrated per batch,
  but run `pytest` and `scripts/visual_review.py` after any image rebuild.
