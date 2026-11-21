# SentinelStream: guide for coding agents

SentinelStream (SS) is a personal research assistant for a long-horizon US stock investor. It alerts on material SEC filings for a watchlist, explains them from the filings themselves, shows what has historically happened after similar events, and keeps a decision ledger measured against a benchmark.

Read `docs/PRD.md` for the full product spec and milestones. This file is the working agreement for anyone (human or agent) writing code here.

## Current milestone: M0, showcase repo on AWS (target: week of 23 Nov 2026)

Done when every box is ticked:

- [x] Repo skeleton in the target layout below, with `.env.example` and a `.gitignore` that excludes every `.env*` except the example
- [ ] CI on GitHub Actions: pytest, Vitest, Ruff, Black check, ESLint, and **gitleaks** on every push and PR
- [ ] Single baseline schema migration (the prototype's 35 migrations collapsed into `001_baseline.sql`), run by the locked migration runner
- [ ] Core prototype modules carried over and running under Docker Compose: orchestration (scheduler, `task_runs`, locks, dispatch), ingestion (SEC client, Massive bars), migrations runner, minimal web UI with the task-runs view
- [ ] Terraform for stage-1 AWS (us-east-1): ECR, EC2 t4g.micro, IAM role for GitHub OIDC, SSM (Run Command + Parameter Store), CloudWatch Logs + one alarm, S3 bucket, AWS Budgets alarm
- [ ] Deploy job: merge to `main` → build images → push to ECR (tagged by commit SHA) → SSM Run Command pulls and restarts Compose on EC2
- [ ] README with a one-paragraph pitch, architecture diagram (Mermaid), screenshots, and a "Scope and roadmap" section

Next milestone after M0 is **M1: Alerts live** (see `docs/PRD.md`). Don't start M1 work until M0 is done.

## Architecture (stage 1)

One EC2 instance runs Docker Compose with four services: `api` (FastAPI, also serves the built frontend), `scheduler`, `worker`, `postgres`. GitHub Actions authenticates to AWS with OIDC, so no AWS keys are stored anywhere. Secrets live in SSM Parameter Store and are injected at container start.

```
GitHub Actions ──push──► ECR ──pull──► EC2 (Docker Compose: api, scheduler, worker, postgres)
       └──────deploy via SSM Run Command──────┘          │
                                     CloudWatch Logs ◄────┤
                                     S3 (filing archive, DB backups) ◄─┘
```

Stage 2 (RDS, ECS Fargate, S3 + CloudFront) is optional and comes after M4. Don't add a NAT gateway or load balancer in stage 1: they dominate cost at this size.

## Target repo layout

```
sentinelstream/
  ingestion/        # providers, normalisers, raw → normalised
  orchestration/    # scheduler, task_runs, locks, dispatch
  alerts/           # materiality rules, notifiers (M1)
  research/         # as-of data access, event studies, evaluation (M2)
  filings/          # XBRL and filing diff (M3)
  ledger/           # decisions and performance (M4)
  api/              # FastAPI app
  web/              # React + TypeScript frontend
  infra/
    terraform/      # AWS stage 1
    compose/        # docker-compose files
    migrations/     # baseline + forward-only SQL
  docs/             # PRD, architecture, decision records (docs/decisions/NNNN-title.md)
  .github/workflows/
```

## Carry-over from the prototype

The private prototype (`sentinelstream-prototype`) has working code. **Copy and adapt it; don't rewrite it.** Keep the app runnable after every step.

| Carry over | Leave in the prototype |
| --- | --- |
| Ingestion: SEC EDGAR client, Massive bars, Finnhub (later) | AI market brief / Market Pulse |
| Raw → normalised boundary, lineage, idempotent upserts | Topics, Research browser, financial dictionary |
| Scheduler, `task_runs`, advisory locks, dispatch, run reconciliation | Holdings and the RLS unlock flow |
| Rate limiting, retries with backoff, `Retry-After` handling | Two-pass event LLM pipeline |
| Rules-based sentiment, outcome evaluation | Discovery-engine experiments, FinBERT scorer (and torch/transformers) |
| Company Context (SEC + structured LLM outputs) | Java/Spring and Redis code from early history |
| Task Operations UI, session auth | |

## Ground rules

1. **Secrets never enter git.** Only `.env.example` with placeholder values. gitleaks runs in CI and as a pre-commit hook. AWS access from CI is OIDC only.
2. **Point-in-time correctness.** Every fact row carries `known_at` (when it became public: EDGAR acceptance time, bar close, article publish time). Research code reads data only through the as-of access layer, which enforces `known_at < as_of`. Tests must prove that lookahead is blocked.
3. **Idempotent writes.** Upsert on stable business keys (accession number, provider ID, symbol + trading date). Every job must be safe to rerun.
4. **All background work goes through the scheduler.** Manual triggers insert pending `task_runs` rows and return `202`. Overlapping runs end as `skipped` under advisory locks. Runs end in explicit `succeeded`, `partial`, `skipped` or `failed` states.
5. **Be a good citizen to data providers.** SEC requests send a descriptive User-Agent from config and are rate-limited client-side. All HTTP calls retry with exponential backoff and honour `Retry-After`. Missing credentials fail the job with a clear error, never with placeholder data.
6. **Numbers come from data, not models.** Financial figures come from SEC XBRL. Any LLM output is schema-validated, and any claim without a citation to a filing section is rejected.
7. **No trading.** No broker integration, no order code, no buy/sell recommendation language in the UI or alerts.
8. **Small, demoable steps.** Small PRs, each leaving the app runnable. Every Friday the `main` branch must be demoable.

## Product decisions already made

- **Watchlist seed:** NVDA, SPCX (SpaceX), ANT (Anthropic), GOOGL (Alphabet), AMZN, NBIS (Nebius). More tickers are added later through the onboarding job.
- **Filer types differ.** Domestic filers produce 10-K, 10-Q, 8-K and Form 4. Foreign private issuers (NBIS) produce 20-F and 6-K and no Form 4. Alert rules must branch on filer type.
- **Benchmark:** QQQ, with SPY shown for reference.
- **AWS:** new account, Free plan, region us-east-1.
- **Alerts:** Slack incoming webhook. Keep the notifier behind an interface so email or Telegram can be added later.
- **Materiality rules v0:** 8-K items 1.01, 2.02, 4.02 and 5.02, plus Form 4 open-market purchases (code P), are high and pushed. Everything else is low and shown in the UI only.
- **Ticker onboarding job:** validate symbol → map to SEC CIK → detect filer type → backfill 2 years of filing metadata and price bars from listing date → pull XBRL company facts → add to the poller's watch set. Status shown as pending, backfilling, ready.

## Stack and conventions

- **Backend:** Python 3.11, FastAPI, Pydantic v2, httpx, psycopg2. Tests with pytest; format with Black, lint with Ruff.
- **Frontend:** React 18, TypeScript, Vite, Ant Design, Tailwind CSS. Tests with Vitest; ESLint and Prettier.
- **Database:** PostgreSQL. Forward-only SQL migrations with a `schema_migrations` ledger, applied under an advisory lock at startup.
- **Infra:** Terraform, Docker Compose, GitHub Actions.
- **Commits:** imperative subject line, one logical change per commit.
- **Decision records:** any non-obvious architecture choice gets a short `docs/decisions/NNNN-title.md` (context, decision, consequences).

## Definition of done for any change

- Tests added or updated, and the full suite passes locally and in CI
- Lint and format checks pass; gitleaks is clean
- No new secrets, no placeholder data paths
- README or docs updated if behaviour or setup changed
- The app still starts with `docker compose up`
