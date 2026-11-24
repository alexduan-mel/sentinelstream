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

The migration tests need a disposable Postgres; without `TEST_DATABASE_URL` they are skipped locally (CI always runs them).

```sh
docker run --rm -d --name ss-test-pg -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test postgres:16
export TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres
uv run pytest && uv run ruff check . && uv run black --check .
(cd web && npm test && npm run lint && npm run format:check)
uvx pre-commit install           # gitleaks, Ruff and Black on every commit
```

## Deploy

Every push to `main` that passes CI deploys automatically ([`deploy.yml`](.github/workflows/deploy.yml)):

1. Build the arm64 image natively and push it to ECR, tagged with the full commit SHA.
2. Send [`infra/deploy/deploy.sh`](infra/deploy/deploy.sh) and the Compose files to the EC2 host with SSM Run Command.
3. On the host: load secrets from SSM Parameter Store into a tmpfs env file, run `docker compose up --wait`, and require `/healthz` to report the new SHA. If it doesn't, the previous image is started again.

AWS access is OIDC only, with no stored keys. To roll back, run the **Deploy** workflow by hand with an older commit SHA. Its image is still in ECR, so nothing is rebuilt.

```sh
gh workflow run deploy.yml --ref main -f image_tag=<full-commit-sha>
```

Infrastructure is in [`infra/terraform`](infra/terraform/README.md).

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
