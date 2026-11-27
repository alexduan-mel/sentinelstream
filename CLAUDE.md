# SentinelStream: guide for coding agents

SentinelStream (SS) is a personal research assistant for a long-horizon US stock investor. It alerts on material SEC filings for a watchlist, explains them from the filings themselves, shows what has historically happened after similar events, and keeps a decision ledger measured against a benchmark.

Read `docs/PRD.md` for the full product spec and milestones. This file is the working agreement for anyone (human or agent) writing code here.

## Current milestone: M2, evidence harness (14 Dec 2026 – 10 Jan 2027)

M2 gives every high alert a base rate: how stocks moved, compared with QQQ, after similar past events. It is done when every box is ticked:

- [x] As-of access layer: research code reads facts only through `research/asof.py`, which enforces `known_at < as_of`; tests prove lookahead is blocked (ADR 0011)
- [x] Market-wide daily bars for every US stock, as traded, two years back, backfilled in chunks that never hold up the SEC poll, then updated daily (ADR 0012)
- [ ] Market-wide insider purchases (Form 4 code P) from SEC's Insider Transactions Data Sets
- [ ] Base-rate calculator: returns 1, 5, 20 and 60 sessions after an event, compared with QQQ (SPY shown), after 0.10% costs per trade, with sample size, median, average, hit rate and a confidence range
- [ ] Insider-buying effect reproduced: a write-up in `docs/` with the result, whichever way it comes out
- [ ] Market-wide 8-K item history
- [ ] A base rate on each high alert, on the Alert detail page and in Slack, pooled across similar stocks when the sample is small
- [x] Split detection: a daily check of Massive's splits endpoint triggers a re-fetch (promised in ADR 0002); market-wide split data for research (ADR 0013)

Next milestone after M2 is **M3: Explain** (see `docs/PRD.md`).

### M1, alerts live: one box left

M1 is built: the SEC poller every 5 minutes, filing-to-issuer links, materiality rules v0, the Slack notifier, the Alerts, Alert detail and Watchlist pages, ticker onboarding and the `task_run_failed` alarm line. Its last box waits for a watchlist company to file a qualifying 8-K (owner's decision, 27 Nov 2026). Tick it when that happens:

- [ ] A real 8-K from a watchlist company reaches Slack within 10 minutes of EDGAR acceptance, checked from stored acceptance and delivery times

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
- **Filer types differ.** Domestic filers produce 10-K, 10-Q, 8-K and Form 4. Foreign private issuers (NBIS) produce 20-F and 6-K, and their insiders are usually exempt from Form 4 (but NBIS insiders have filed Forms 3 and 4 since March 2026). Current-report alert rules (8-K against 6-K) branch on filer type; the Form 4 rule applies to every filer type (ADR 0008).
- **Benchmark:** QQQ, with SPY shown for reference.
- **AWS:** new account, Free plan, region us-east-1.
- **Alerts:** Slack incoming webhook. Keep the notifier behind an interface so email or Telegram can be added later.
- **Materiality rules v0:** 8-K items 1.01, 2.02, 4.02 and 5.02, plus Form 4 open-market purchases (code P), are high and pushed. Everything else is low and shown in the UI only.
- **Ticker onboarding job:** validate symbol → map to SEC CIK → detect filer type → backfill 2 years of filing metadata and price bars from listing date → pull XBRL company facts → add to the poller's watch set. Status shown as pending, backfilling, ready.

## Stack and conventions

- **Backend:** Python 3.11, FastAPI, Pydantic v2, httpx, psycopg2. Tests with pytest; format with Black, lint with Ruff.
- **Frontend:** React 18, TypeScript, Vite, Ant Design, Tailwind CSS. Tests with Vitest; ESLint and Prettier. Read `web/DESIGN.md` before any UI work; it overrides the reference screenshots.
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
