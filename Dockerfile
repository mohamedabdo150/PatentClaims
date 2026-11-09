# syntax=docker/dockerfile:1.7
# Patent Claims Converter - production image (Ubuntu 24.04 LTS, Python 3.12).
#
# LibreOffice (headless "nogui" packages) renders EMF/WMF, drawing groups, VML shapes and
# equation fallbacks in isolated subprocesses; poppler rasterizes the result. Fonts cover
# Latin, Greek, mathematical symbols and Arabic so rendered snippets use correct glyphs.
#
# Optional corporate / TLS-inspecting proxy CA for build-time downloads (apt + pip). It is
# used only while building and is never copied into the image:
#   docker build --secret id=build_ca,src=/path/to/ca.pem .
FROM ubuntu:24.04 AS base
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8
# Shared helper: switch apt to HTTPS + build CA only when the build secret is provided.
RUN printf '%s\n' \
      '#!/bin/sh' \
      'set -e' \
      'if [ -f /run/secrets/build_ca ]; then' \
      '  install -m 0644 /run/secrets/build_ca /tmp/build-ca.pem' \
      '  sed -i "s|http://\(archive\|security\|ports\).ubuntu.com|https://\1.ubuntu.com|g" /etc/apt/sources.list.d/ubuntu.sources' \
      '  echo "Acquire::https::CAInfo \"/tmp/build-ca.pem\";" > /etc/apt/apt.conf.d/99build-ca' \
      'fi' \
      'apt-get update' \
      'apt-get install -y --no-install-recommends "$@"' \
      'rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* /etc/apt/apt.conf.d/99build-ca /tmp/build-ca.pem' \
      'sed -i "s|https://\(archive\|security\|ports\).ubuntu.com|http://\1.ubuntu.com|g" /etc/apt/sources.list.d/ubuntu.sources' \
      > /usr/local/sbin/apt-install && chmod 0755 /usr/local/sbin/apt-install
RUN --mount=type=secret,id=build_ca,required=false \
    apt-install python3.12 ca-certificates

FROM base AS builder
RUN --mount=type=secret,id=build_ca,required=false apt-install python3.12-venv
WORKDIR /build
COPY requirements.txt .
RUN --mount=type=secret,id=build_ca,required=false \
    python3.12 -m venv /opt/venv \
    && if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && /opt/venv/bin/pip install -r requirements.txt

FROM base AS runtime
ARG APP_UID=10001
RUN --mount=type=secret,id=build_ca,required=false \
    apt-install \
        libreoffice-writer-nogui libreoffice-draw-nogui libreoffice-math-nogui \
        poppler-utils \
        fonts-dejavu-core fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea \
        fonts-noto-core fonts-hosny-amiri fonts-stix
RUN groupadd --system --gid ${APP_UID} app \
    && useradd --system --uid ${APP_UID} --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /data/patent-processing/spool \
    && chown -R app:app /data/patent-processing \
    && chmod 0700 /data/patent-processing /data/patent-processing/spool
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
