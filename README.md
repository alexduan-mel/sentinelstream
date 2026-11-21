# SentinelStream

A personal research assistant for a long-horizon US stock investor. It alerts on material SEC filings for a watchlist, explains them from the filings themselves, shows what has historically happened after similar events, and keeps a decision ledger measured against a benchmark. See [`docs/PRD.md`](docs/PRD.md).

> Work in progress: milestone M0 (repo, CI and AWS deployment). A full README with an architecture diagram and screenshots lands at the end of M0.

## Run locally

Requires Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```sh
cp .env.example .env
docker compose up --build        # API and web UI on http://127.0.0.1:8000
curl http://127.0.0.1:8000/healthz
```

Development without Docker:

```sh
uv sync                          # Python 3.11 env with dev tools
uv run uvicorn api.app:app --reload
(cd web && npm ci && npm run dev) # Vite on :5173, proxies the API
```

## Checks

```sh
uv run pytest && uv run ruff check . && uv run black --check .
(cd web && npm test && npm run lint && npm run format:check)
uvx pre-commit install           # gitleaks, Ruff and Black on every commit
```

## Layout

| Path | Purpose |
| --- | --- |
| `ingestion/` | Providers, normalisers, raw → normalised |
| `orchestration/` | Scheduler, task runs, locks, dispatch |
| `alerts/`, `research/`, `filings/`, `ledger/` | Later milestones (M1–M4) |
| `api/` | FastAPI app; also serves the built web UI |
| `web/` | React + TypeScript frontend |
| `infra/` | Terraform, Compose files, SQL migrations |
| `docs/` | PRD and decision records |
