# syntax=docker/dockerfile:1.7
# Build from the repo root:  docker build -f docker/Dockerfile .
# Or via Compose:             docker compose up

FROM python:3.12-slim

# Runtime deps:
#   sqlite3 — used by docker/entrypoints/maintenance.sh (VACUUM/ANALYZE)
#   curl/ca-certificates — supercronic download + healthcheck
#   tini — proper PID 1 / signal forwarding
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        sqlite3 ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

# supercronic — a cron daemon designed for containers. Logs to stdout,
# forwards signals, doesn't fork. Mirrors the existing crontab.demo and
# the launchd plists, but inside the container.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=0.2.29
RUN curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
 && chmod +x /usr/local/bin/supercronic

WORKDIR /app

# Install the package. Copy only what we need so wheel-builds don't
# invalidate the layer cache when unrelated files change.
COPY pyApi/pyproject.toml pyApi/VERSION /app/pyApi/
COPY pyApi/stock_toolkit /app/pyApi/stock_toolkit
COPY pyApi/tests /app/pyApi/tests
# Normalize permissions: source file modes follow the host umask, which
# can leave a stray 0600 file unreadable by the non-root runtime user.
RUN chmod -R go+rX /app/pyApi \
 && pip install --no-cache-dir /app/pyApi

# Entrypoints + scheduler crontab (kept under /app/docker so a future
# native Mac wrapper can reuse maintenance.sh without rewriting it).
COPY docker/crontab            /app/docker/crontab
COPY docker/entrypoints/       /app/docker/entrypoints/
RUN chmod +x /app/docker/entrypoints/*.sh

# Non-root runtime user. UID 1000 matches the default host user on Linux
# desktops and macOS Docker Desktop; data files written into /data will
# end up owned by 1000:1000 on the host bind mount.
RUN useradd -m -u 1000 stock \
 && mkdir -p /data && chown stock:stock /data
USER stock

ENV STOCK_DIR=/data \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    # bind Streamlit to all interfaces so the host port-forward works,
    # and suppress its first-run prompts (no email, no usage stats).
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

VOLUME ["/data"]
EXPOSE 8501

# Default service = the dashboard. The collector service in compose.yaml
# overrides this with the supercronic entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["stock-ui"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8501/_stcore/health || exit 1
