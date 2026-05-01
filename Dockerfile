FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

# Install codex CLI (override at build time if needed).
ARG CODEX_INSTALL_CMD="echo 'WARNING: provide --build-arg CODEX_INSTALL_CMD to install codex'"
RUN sh -c "${CODEX_INSTALL_CMD}"

COPY pyproject.toml README.md AGENTS.md ./
COPY codex_discord_mcp ./codex_discord_mcp

RUN pip install --upgrade pip && pip install .

RUN useradd --create-home --shell /bin/bash app \
 && mkdir -p /data \
 && chown -R app:app /app /data
USER app

ENV CODEX_DB_PATH=/data/codex_discord.db \
    CODEX_WORKDIR=/data/workspace \
    HEALTH_PORT=8080 \
    METRICS_PORT=9090 \
    ENABLE_HEALTH=1 \
    ENABLE_METRICS=1 \
    LOG_JSON=1

EXPOSE 8080 9090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/healthz || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["codex-discord-bot"]
