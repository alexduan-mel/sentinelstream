# SentinelStream

SentinelStream is a personal research assistant for a long-horizon US stock investor. It watches SEC EDGAR for new filings from the companies on a watchlist and flags the material ones. It explains each filing from the filing itself, with numbers taken from XBRL rather than from a model. It shows what has historically followed similar events, and it keeps a decision ledger measured against QQQ. It is a single-user tool that runs as a small, monitored production system on AWS: every merge to `main` is tested, built and deployed automatically, the infrastructure is defined in Terraform, and no credentials are stored in the repo or in CI. It makes no trades and gives no buy or sell recommendations. The full spec is in [`docs/PRD.md`](docs/PRD.md).

![Alerts page: watchlist filings, newest first, with 8-K items, materiality, Slack delivery and when the poller last ran](docs/screenshots/alerts.png)

<details>
<summary>More screenshots: an alert's detail, Watchlist, Tasks, a run expanded, and Sign in</summary>

![Alert detail: timings from EDGAR acceptance to Slack, the companies the filing is listed under, and its Form 4 transactions](docs/screenshots/alert-detail.png)

![Watchlist: tracked tickers with filer type, CIK, onboarding status and last filing, then the benchmarks](docs/screenshots/watchlist.png)

![Tasks page: task definitions and run history](docs/screenshots/tasks.png)

![A run expanded: trace ID, per-issuer meta and Run again](docs/screenshots/tasks-expanded.png)

![Sign in](docs/screenshots/signin.png)

</details>

Screenshots are from the live EC2 deployment at 1440 px, with times in ET (the page can also show the viewer's own time zone).

## Architecture

Stage 1 runs everything on one EC2 instance with Docker Compose, which keeps cost within the US$30-a-month target. There is no NAT gateway, no load balancer, and no inbound port. The UI is reached through an SSM port forward.

```mermaid
flowchart LR
  dev([Developer]) -->|push / merge| gh[GitHub Actions<br/>CI: pytest, Vitest, Ruff, Black,<br/>ESLint, gitleaks, Terraform]
  gh -->|OIDC, no stored keys| iam[IAM deploy role]
  gh -->|image tagged by commit SHA| ecr[(ECR)]
  gh -->|SSM Run Command: deploy.sh| ec2

  subgraph ec2[EC2 t4g.micro · Docker Compose]
    api[api<br/>FastAPI + built React UI]
    sched[scheduler<br/>enqueues due runs]
    worker[worker<br/>claims and runs tasks]
    pg[(Postgres 16<br/>on EBS volume)]
    api --> pg
    sched --> pg
    worker --> pg
  end

  ecr -->|pull| ec2
  ssm[(SSM Parameter Store<br/>secrets)] -->|env at deploy time| ec2
  ec2 -->|container logs| cw[CloudWatch Logs<br/>+ task_run_failed alarm]
  worker -->|rate-limited, Retry-After| sec[SEC EDGAR<br/>filings, Form 4 XML, XBRL]
  worker --> massive[Massive<br/>daily bars, ticker details]
  worker -->|high filings, once each| slack[Slack webhook]
  api -->|check a ticker before adding it| sec
  you([Investor]) -->|SSM port forward| api
```

- **All background work goes through `task_runs`** ([ADR 0003](docs/decisions/0003-scheduler-and-worker.md)). The scheduler only enqueues. The worker claims runs with `FOR UPDATE SKIP LOCKED` and holds a per-task advisory lock, so an overlapping run ends `skipped`. The "Run now" button and the CLI insert the same `pending` rows, and the API answers 202.
- **Every fact row carries `known_at`,** the time it became public: EDGAR acceptance for filings, the 16:00 ET close for daily bars. Writes upsert on stable business keys, so every job is safe to rerun ([ADR 0002](docs/decisions/0002-baseline-data-model.md), [ADR 0004](docs/decisions/0004-sec-filings-ingestion.md), [ADR 0006](docs/decisions/0006-edgar-acceptance-time-is-utc.md)).
- **Data providers are treated politely.** SEC requests carry a declared User-Agent and are rate-limited on the client side. Every HTTP call retries with exponential backoff and honours `Retry-After`. A missing credential fails the run with a clear error instead of falling back to placeholder data.
- **Alerts are decided by plain rules, per company** ([ADR 0007](docs/decisions/0007-filing-issuer-links.md), [ADR 0008](docs/decisions/0008-materiality-rules-v0.md)). A filing is linked to every watchlist company EDGAR lists it under. It is high for a company when it is an 8-K with item 1.01, 2.02, 4.02 or 5.02 from a domestic filer, or a Form 4 open-market purchase of that company's shares, read from the Form 4 XML. Everything else is low and only shown in the UI.
- **Each high filing reaches Slack once** ([ADR 0009](docs/decisions/0009-slack-notifier.md)). An outbox keyed on accession number and channel deduplicates. History and stale filings are never pushed. Delivery happens in the same 5-minute poll that found the filing, and each alert records how long it took from EDGAR acceptance.
- **Tickers are onboarded, not hand-seeded** ([ADR 0010](docs/decisions/0010-ticker-onboarding.md)). A symbol is checked with SEC before it is added, so the watchlist only holds real companies. A job then ties it to its SEC issuer, cross-checks it with the price provider, and loads two years of filings, daily prices (from the listing date, since a ticker can be reused) and XBRL facts, moving it from pending to backfilling to ready.
- **Failures page someone.** Every failed run writes one `task_run_failed` log line, which a CloudWatch alarm turns into an email.
- **Sign-in** uses PBKDF2 password hashes, an HMAC-signed HttpOnly cookie, and a login lockout. Every `/api` route except login and logout requires a session ([ADR 0005](docs/decisions/0005-session-auth.md)).

Other decisions are recorded in [`docs/decisions/`](docs/decisions).

## Scope and roadmap

| Milestone | Status | What it delivers |
| --- | --- | --- |
| **M0** Showcase repo on AWS | Done, Nov 2026 | CI with gitleaks, Terraform for stage 1, auto-deploy to EC2 on merge, scheduler and worker, SEC filings and daily price ingestion, sign-in, the Tasks page, and a read-only Alerts list of stored filings |
| **M1** Alerts live | Built; waiting on a live 8-K | Materiality rules v0 (8-K items 1.01, 2.02, 4.02, 5.02 and Form 4 open-market purchases), Slack alerts deduplicated by accession number, ticker onboarding with the Watchlist page, materiality and Slack delivery on the Alerts page, an Alert detail page with acceptance-to-Slack timing, and the `task_run_failed` alarm. The last check, a real watchlist 8-K reaching Slack within 10 minutes of EDGAR acceptance, waits for one to be filed |
| **M2** Evidence harness | Planned | As-of data access with tests proving lookahead is blocked, event studies with costs, and a base rate (with sample size) on every alert |
| **M3** Explain | Planned | XBRL fundamentals and a year-over-year 10-K diff with citations to filing sections |
| **M4** Journal | Planned | Decision ledger, and returns against QQQ split into alpha and beta |
| AWS stage 2 | Optional | RDS, ECS Fargate, S3 and CloudFront |

**Out of scope:** trade execution and broker connections; buy or sell recommendations; options, intraday data and order books; charting, screeners and a general fundamentals UI; and multi-user SaaS. Broker apps and TradingView already cover those well.

**Watchlist:** NVDA, SPCX (SpaceX), ANT (Anthropic), GOOGL, AMZN and NBIS (Nebius, a foreign private issuer that files 20-F and 6-K, though its insiders file Form 4), with QQQ as the benchmark and SPY for reference. More tickers are added on the Watchlist page.

## Run locally

Requires Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```sh
cp .env.example .env             # then set AUTH_SESSION_SECRET and AUTH_ADMIN_PASSWORD
docker compose up --build        # Postgres 16, then api (web UI on http://127.0.0.1:8000), scheduler and worker
curl http://127.0.0.1:8000/healthz
```

Open http://127.0.0.1:8000 and sign in as `admin` with `AUTH_ADMIN_PASSWORD`. The first login stores a hash of that password. Ingestion runs need `SEC_USER_AGENT` (an application name and a contact email, as SEC requires) and `MASSIVE_API_KEY`. Without them a run fails with a clear configuration error. High alerts go to Slack through `SLACK_WEBHOOK_URL`; while it is empty, a poll with an alert to send ends `partial` and the alert waits. On EC2 all of these settings come from Parameter Store, and they are read at deploy time.

Background work goes through `task_runs` ([ADR 0003](docs/decisions/0003-scheduler-and-worker.md)): the `scheduler` enqueues due tasks as pending runs, and the `worker` claims and executes them. Adding a ticker (`POST /api/instruments`) queues `onboard_tickers`, which ties it to its SEC issuer and loads two years of filings, daily prices and XBRL facts before marking it ready ([ADR 0010](docs/decisions/0010-ticker-onboarding.md)). Queue a run with **Run now** on the Tasks page, or from the command line:

```sh
docker compose exec worker python -m orchestration.enqueue ingest_sec_filings   # prints the run id
docker compose logs -f worker
```

Every app container applies pending SQL migrations from [`infra/migrations`](infra/migrations) before it starts, under a Postgres advisory lock ([ADR 0002](docs/decisions/0002-baseline-data-model.md)). To run them by hand: `uv run python -m orchestration.migrations` with the `POSTGRES_*` settings exported.

Development without Docker:

```sh
uv sync                          # Python 3.11 env with dev tools
uv run uvicorn api.app:app --reload
(cd web && npm ci && npm run dev) # Vite on :5173, proxies the API
```

## Checks

The database tests (migrations, task runs, scheduler, worker, API) need a disposable Postgres; without `TEST_DATABASE_URL` they are skipped locally (CI always runs them).

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
3. On the host: load secrets from SSM Parameter Store into a tmpfs env file, check the EBS data volume is mounted, run `docker compose up --wait` (each app container applies pending migrations as it starts, and api, scheduler and worker must all report healthy), and require `/healthz` to report the new SHA. If it doesn't, the previous image is started again.

AWS access is OIDC only, with no stored keys. To roll back, run the **Deploy** workflow by hand with an older commit SHA. Its image is still in ECR, so nothing is rebuilt.

```sh
gh workflow run deploy.yml --ref main -f image_tag=<full-commit-sha>
```

Infrastructure, and how to reach the host and the UI over SSM, is in [`infra/terraform`](infra/terraform/README.md).

## Layout

| Path | Purpose |
| --- | --- |
| `ingestion/` | Providers (SEC, Massive), normalisers, the SEC poller, price bars, ticker onboarding |
| `orchestration/` | Scheduler, task runs, locks, dispatch, migrations |
| `alerts/` | Materiality rules, classification, the Slack notifier and its outbox |
| `filings/` | 8-K item titles, Form 4 XML, XBRL company facts, EDGAR links |
| `research/`, `ledger/` | Later milestones (M2, M4) |
| `api/` | FastAPI app; also serves the built web UI |
| `web/` | React + TypeScript frontend |
| `infra/` | Terraform, Compose files, SQL migrations |
| `docs/` | PRD, decision records, screenshots |
