# Deployment (initial – hardened in Phase 7)

## Image
* `python:3.12.8-slim-bookworm`, multi-stage, pinned `requirements.txt`, non-root uid 10001.
* Read-only root filesystem supported; writable volume `/data/patent-processing` (jobs + upload spool via `TMPDIR`).
* `HEALTHCHECK` → `/health`. Startup validates configuration and claim patterns and exits on error.
* Optional build secret `pip_ca` for TLS-inspecting proxies (not copied into the image).

Verified in this phase: image builds; production boot without API key is refused;
container runs as `app` with `--read-only --cap-drop ALL --security-opt no-new-privileges`;
health becomes `healthy`; 2 × 49 MB conversion request validated; all 78 tests pass
inside the image against the pinned dependency versions.

## Two-node layout (target)

```
           OutSystems ──HTTPS──▶ Load balancer (TLS, /ready health check, sticky: none)
                                   │                      │
                       node A: docker compose     node B: docker compose
                       WORKERS × 1 job slot        WORKERS × 1 job slot
                       /mnt/nvme → /data/...       /mnt/nvme → /data/...
```

* `/ready` returns 503 while a worker's job slot is occupied → the LB sends the next job to the other node.
  With `WORKERS > 1`, readiness reflects the worker that answered the probe; size `WORKERS` so that
  `WORKERS × peak job RSS` fits the container memory limit (to be measured in Phase 6).
* Rate limiting is per worker; also enforce request rate and body size at the LB.
* LB/proxy: allow ≥ 201 MB request bodies, response buffering disabled or sized for the
  final response, idle timeout ≥ `max_processing_seconds` + margin.

## Run
```bash
cp .env.example .env   # set PATENT__SECURITY__API_KEY_SHA256
docker compose up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build   # development
```
