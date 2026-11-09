# syntax=docker/dockerfile:1.7
# Patent Claims Converter - production image (Phase 2 scope: API + capability analyzer).
# EMF/WMF conversion tooling (Phase 4) will be added as an isolated layer when implemented.
FROM python:3.12.8-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS builder
WORKDIR /build
COPY requirements.txt .
# Optional corporate/proxy CA for pip only (never copied into the image):
#   docker build --secret id=pip_ca,src=/path/to/ca.pem .
RUN --mount=type=secret,id=pip_ca,required=false \
    python -m venv /opt/venv \
    && if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi \
    && /opt/venv/bin/pip install -r requirements.txt

FROM base AS runtime
ARG APP_UID=10001
RUN groupadd --system --gid ${APP_UID} app && useradd --system --uid ${APP_UID} --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/patent-processing/spool && chown -R app:app /data/patent-processing && chmod 0700 /data/patent-processing /data/patent-processing/spool
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root src/ /app/src/
COPY --chown=root:root config/ /app/config/
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PATENT_CONFIG_FILE=/app/config/app.yaml \
    TMPDIR=/data/patent-processing/spool \
    WORKERS=2 \
    PORT=8000
USER app
VOLUME ["/data/patent-processing"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health',timeout=4)" || exit 1
# Startup validation happens in create_app(): invalid config or claim patterns abort the boot.
CMD ["sh", "-c", "exec uvicorn patent_converter.main:create_app --factory --host 0.0.0.0 --port ${PORT} --workers ${WORKERS} --timeout-keep-alive 75 --no-server-header"]
