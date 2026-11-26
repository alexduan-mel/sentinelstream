# SentinelStream UI design

This is the spec for the web UI. The UI is built directly in React with Ant Design. There is no separate design tool: reference screenshots in `web/design/reference/` show the intended feel, and this file says what to keep from them and what to ignore.

## Principles

- **A quiet, dense working tool**, closer to SEC EDGAR or a Bloomberg terminal than to a SaaS dashboard. Information lives in tables and plain text, not cards.
- **Color carries meaning only**: status, materiality, gains and losses. Everything else is neutral.
- **Show only real data.** No placeholder metrics, no invented system telemetry (latency, feed or node names, "LIVE" badges), no fake keyboard shortcuts. If the backend does not provide a value, the UI does not show it.
- **Hide what is unfinished.** No "coming soon" sections or dead controls. A later milestone adds its own UI.
- **No trading language** (ground rule 7): never "buy", "sell", "signal" or "recommendation". Use "filing", "material", "flagged".

## Theme

**Colors.** `web/design/tokens.json` is the only source of color. Tailwind (`tailwind.config.ts`) and Ant Design (`src/antdTheme.ts`) both read from it. Semantic use:

| Color | Used for |
| --- | --- |
| Accent (`semantic.info`) | Links, focus rings, the selected nav tab, the selected row. Nothing decorative. |
| Amber (`semantic.warning`) | High materiality, `partial` runs |
| Red (`semantic.negative`) | `failed` runs, errors, negative returns |
| Green (`semantic.positive`) | `succeeded` runs (muted), positive returns |
| Neutral grey | Low materiality, `pending` and `skipped` runs, all other numbers |

**Typography.** One family in two cuts, self-hosted through `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` (SIL OFL 1.1; no font CDN). The aim is soft and readable, not a terminal look:

- **IBM Plex Sans** for all text, including numbers, times, tickers and form types. Set `font-variant-numeric: tabular-nums` globally so numbers align in columns without a monospace font.
- **IBM Plex Mono** only for identifiers: accession numbers, CIKs, trace IDs, task names and JSON.
- Table headers use normal sentence case, never uppercase or letter-spaced.

| Style | Size / line height | Weight |
| --- | --- | --- |
| Page title | 20 / 28 | 600 |
| Section title | 14 / 20 | 600 |
| Body and table cells | 13 / 20 | 400 |
| Secondary text, tags, table headers | 12 / 16 | 400 (headers 500) |

Nothing is smaller than 12px.

**Size and shape.** These are dense on purpose; they are what keeps Ant Design from looking like a stock admin template.

- Control height 28px (small 24px, large 32px). Table rows about 32px.
- Border radius 2px everywhere, including tags and buttons.
- 8px spacing grid, 4px inside tables and tags. Page padding 24px.
- No shadows. Depth comes from 1px borders and the three background steps in `tokens.json`.
- Reference width 1440px. Content spans the full width, except the sign-in form.

**Changes needed in code** (current values → spec):

- `src/antdTheme.ts`: `controlHeight` 40 → 28, `controlHeightSM` 32 → 24, add `controlHeightLG` 32, `borderRadius` 4 → 2, `borderRadiusLG` 6 → 2, `fontSize` 14 → 13, `fontFamily` Inter → IBM Plex Sans, Table `cellPaddingBlock` 10 → 6, `boxShadowSecondary` → `none`.
- `design/tokens.json`: sans font Inter → IBM Plex Sans, mono font JetBrains Mono → IBM Plex Mono; text styles and radii to the tables above.
- `src/index.css`: `font-variant-numeric: tabular-nums` on `body`.
- `src/index.css`: `.sentinel-tag` font size 10px → 12px.

## Layout

- **Top bar** (48px, full width, bottom border): the text wordmark "SentinelStream" on the left; nav tabs **Alerts, Watchlist, Tasks**, with a 2px underline on the active tab; on the right, a user menu showing the username, with "Sign out". Nothing else goes in the top bar.
- **Page**: a title row (title plus a muted count, e.g. "Alerts · 15"), an optional filter row, then content. Pages scroll normally. There are no docked panes, no command palette and no keyboard-shortcut bar.
- **Times**: stored in UTC, shown in US Eastern. Today: `14:32 ET` with a muted relative time (`8m ago`). Earlier: `Nov 23 14:32 ET`. Durations: `3.4s`.
- **States**: every data view has a loading state (Table `loading`), an empty state (one line of muted text, no illustration) and an error state (inline Ant Design `Alert` with a Retry button).

## Pages

### Sign in (`/login`, M0)

- A 360px form in the centre: the text wordmark, username, password, and a "Sign in" button. No tagline, no illustration, no shadow, no sign-up or password reset.
- Errors appear inline above the button: "Incorrect username or password". On HTTP 429: "Too many attempts. Try again in N minutes", and the button is disabled.

### Tasks (`/tasks`, M0)

Data: `task_definitions`, `task_runs`.

- **Task definitions** table: Task (mono) · Schedule ("Every 5 min", "Weekdays 18:30 ET") · Trigger (Manual / Automatic) · Next run · Last status · a "Run now" button. "Run now" inserts a pending run and the API returns 202 (ground rule 4).
- **Run history** table, every run newest first, filterable by task and status: Started · Task · Mode · Status · Duration · Fetched · Inserted · Deduped (right-aligned, neutral) · Error (one line, truncated; hovering shows the full message).
- Status tags: `succeeded` muted green, `partial` amber, `failed` red, `running` accent, `pending` and `skipped` grey.
- Clicking anywhere on a row (or its +) expands it. The expanded row shows the trace ID (UUID, with a Copy button), the full error message, and `meta` as formatted JSON, plus "Run again", which queues a new run with the same parameters. There are no per-step timings: `task_runs` does not record them.
- While any run is pending or running, the page polls every 10 seconds.

### Alerts (`/`, list in M0, materiality in M1)

Built in M1 except Alert detail. Item shows each 8-K item number with its title from Form 8-K, one per line, and for a high Form 4 the shares bought ("Open-market purchase · 12,400 sh"). A filing linked to several watchlist issuers shows each ticker. Materiality is High if the rules flag it for any linked issuer, and a dash until the filing is classified; hovering the tag shows the rule's reason. Pushed shows the Slack time once delivered, "Queued" while waiting (hover for the last error), "Skipped" for an alert that will never be sent (hover for why), and a dash for filings that were never queued. Hover explanations use Ant Design tooltips, which appear at once; a native `title` waits about a second and is easy to miss. The poll line reads "Polls every 5 min · last ran 2m ago · next in 3m" and refreshes every minute.

Data: `filings` plus the M1 materiality and notification records.

- One table, newest first: Accepted · Ticker · Form · Item (e.g. "Item 5.02 · Departure of directors or officers", "Open-market purchase · 12,400 sh") · Materiality (`High` amber, `Low` neutral) · Pushed (Slack time, or "—") · "SEC ↗" link to the filing.
- One filter row: ticker multi-select, materiality Segmented (All / High / Low), form type select, date range. On the right of the same row, muted text: "Polls every 5 min · next in 3 min".
- High means materiality rules v0 only: 8-K items 1.01, 2.02, 4.02 and 5.02, and Form 4 open-market purchases (code P). Everything else is Low.
- Clicking a row opens Alert detail. No model-written summaries in M1.

### Watchlist (`/watchlist`, M1)

Data: `instruments`, plus the onboarding task run for instruments that are not ready.

- An "Add ticker" input and button at the top. Validation errors appear inline next to it ("XYZQ: not found on any US exchange"). Adding a ticker creates a pending instrument and queues onboarding.
- **Tracked** table: Ticker · Name · Filer type (Domestic / Foreign private) · CIK (mono) · Onboarding (Pending / Backfilling / Ready / Failed) · Last filing (form plus relative time) · "Remove" as a text action with a confirmation. Remove sets `active = false` and keeps history.
- A backfilling row shows a compact Ant Design `Steps`: Validate → Map CIK → Filings (2y) → Prices (2y) → XBRL. A failed row shows `onboarding_error`.
- **Benchmarks** table (QQQ, SPY): Ticker · Name · Price history ("Ready · 2y daily · updated Nov 24 18:30 ET"). Benchmarks cannot be removed.

### Alert detail (`/alerts/:accession`, M1)

- Header: ticker, form, items, accepted time, materiality, pushed time.
- An Ant Design `Descriptions` block: accession number, CIK, filed date, primary document (link to SEC), first seen, detection delay (`first_seen_at - accepted_at`).
- Base rates (M2), key financials (M3) and "Log a decision" (M4) are added by those milestones, not shown as placeholders.

### Ticker deep dive (M3)

Not built until M3.

## Reference screenshots

`web/design/reference/` holds early mockups at 1440px wide. **Follow this file wherever a screenshot disagrees with it.**

| File | Keep | Ignore |
| --- | --- | --- |
| `alerts.png` | Table density, form tags, time column format | Uppercase monospace headers and monospace numbers (see Typography), three-level materiality, "Synthesized summary" column, the wrapped filter rows, the price tape, "LIVE" and latency badges, the shortcut bar and footer |
| `tasks.png` | Definitions table, runs table, status tags, the expanded row with error and JSON | `sync_watchlist_metrics`, "Execution pipeline steps" cards, run and node IDs, Celery, Redis and worker names, green inserted counts, the top bar style |
| `watchlist.png` | Add-ticker input with inline error, onboarding `Steps`, the benchmarks section | Company names for SPCX and ANT (SpaceX, Anthropic), "Section 01 /" labels, the capacity limit, the filer filter, "Refresh status", 5y backfill text, all telemetry |
| `signin.png` | Centred narrow form | The tagline, the "Enter ↵" hint, the shadow |
