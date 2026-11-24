# One image for api, scheduler and worker; Compose picks the command.
# Built for linux/arm64 in CI (EC2 t4g is Graviton) and for the host platform locally.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY api/ api/
COPY ingestion/ ingestion/
COPY orchestration/ orchestration/
COPY alerts/ alerts/
COPY research/ research/
COPY filings/ filings/
COPY ledger/ ledger/
COPY infra/migrations/ infra/migrations/
COPY infra/docker/entrypoint.sh /usr/local/bin/sentinelstream-entrypoint
COPY --from=web /web/dist web/dist

RUN useradd --system --uid 10001 --no-create-home sentinel
USER sentinel

ARG SENTINELSTREAM_VERSION=dev
ENV SENTINELSTREAM_VERSION=$SENTINELSTREAM_VERSION

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --start-interval=2s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
# Migrations run first in every container; see infra/docker/entrypoint.sh.
ENTRYPOINT ["/usr/local/bin/sentinelstream-entrypoint"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
