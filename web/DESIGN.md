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

- **Top bar** (48px, full width, bottom border): the text wordmark "SentinelStream" on the left; nav tabs **Alerts, Watchlist, Tasks, Glossary**, with a 2px underline on the active tab; on the right, a user menu showing the username, with "Sign out". Left of the user menu, a small ET | AEDT toggle for the time zone (hidden when the browser's own zone is US Eastern). Nothing else goes in the top bar.
- **Page**: a title row (title plus a muted count, e.g. "Alerts · 15"), an optional filter row, then content. Pages scroll normally. There are no docked panes, no command palette and no keyboard-shortcut bar.
- **Times**: stored in UTC. Shown in US Eastern by default (`14:32 ET`), or in the browser's own zone (`14:32 AEDT`; AEST in winter, as Intl follows daylight saving) when the viewer picks it with the ET | AEDT toggle in the top bar. The choice is remembered per browser. Today: the clock time with a muted relative time (`8m ago`). Earlier: `Nov 23 14:32 ET`. Hovering a time shows the same moment in the other zone. Schedules tied to the US market keep ET ("Weekdays 18:30 ET"). The accepted-date filter uses calendar days in the zone shown. Durations: `3.4s`.
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
- Each row ends with a **Details** link (opens Alert detail) and "SEC ↗" (opens EDGAR); clicking anywhere else on the row opens Alert detail too, unless text is being selected. Alert detail is a full page, not a side panel: its Form 4 and base-rate tables need the width.
- The filters live in the address (`/?ticker=NVDA&materiality=high&page=2`), and a filter change replaces it rather than adding a history step. So "← Alerts", the browser's Back button and bookmarks all return to the same filtered list, at the same scroll position. No model-written summaries in M1.

### Watchlist (`/watchlist`, M1)

Data: `instruments` with their onboarding status (ADR 0010), newest filing and price coverage.

- An "Add ticker" input (what is typed is uppercased) and button at the top. The API checks the symbol with SEC before storing anything, so the table only ever holds real tickers. A refusal appears inline next to the input, and nothing is added: "XYZQ: not found on any US exchange", a fund, a duplicate ("NVDA is already on the watchlist"), a benchmark, a malformed symbol, or "Could not reach SEC to check MSFT. Try again in a minute." A valid ticker appears at once with its name and SEC identity, as Pending, and onboarding loads its history.
- **Tracked** table: Ticker · Name · Filer type (Domestic / Foreign private) · CIK (mono) · Onboarding (Pending grey / Backfilling accent / Ready muted green / Failed red) · Last filing (form plus time) · actions. Under the status, one truncated line gives `onboarding_error` (red for Failed, amber for a transient error while Backfilling), with the full text on hover. "Retry" appears for Failed rows and rows with an error. "Remove" is a neutral text action with a confirmation; it sets `active = false` and keeps history.
- Plain-language help on hover: an ⓘ next to CIK, Filer type and Onboarding explains the column (the Onboarding one gives the flow, Pending → Backfilling → Ready, or Failed), and each status tag says what it means and what happens next. A Backfilling row with an error says it waits for the daily setup run or Retry, instead of "Next: Ready".
- No per-step progress: `instruments` records the status, not the step in progress, and a ticker takes seconds. The page refreshes every 5 seconds while any ticker is pending or backfilling.
- **Benchmarks** table (QQQ, SPY): Ticker · Name (from Massive; an ETF has no SEC issuer) · Price history (status, then "501 daily bars · Nov 26, 2024 – Nov 25, 2026"). Benchmarks cannot be removed.

### Alert detail (`/alerts/:accession`, M1)

Opened by clicking a row on Alerts (the "SEC ↗" link still opens EDGAR). The Alerts tab stays selected.

- "← Alerts" back link, then the header: tickers and form ("AMZN · Form 4"), the materiality tag, each 8-K item with its title or the shares bought, and for a high filing "Flagged:" with the rule that matched.
- An Ant Design `Descriptions` block: accepted by EDGAR, pushed to Slack (time, Queued or Skipped, with the reason on hover), first seen by the poller, detection delay (`first_seen_at - accepted_at`; "History" for filings loaded as history, where the gap says nothing about polling), acceptance to Slack (the M1 10-minute target), Slack attempts, accession number (mono, copyable), filer CIK, filing date, period of report, primary document and the filing index (links to SEC).
- **Companies it is listed under**: one row per linked issuer with its own materiality, the rule's reason, whether it was news or history for that issuer, and when it was first seen.
- **Form 4 transactions** (only when stored): reporting owner and role, security, date, code with SEC's meaning ("P · Open-market or private purchase"), shares (+ acquired, − disposed), price, shares owned after, direct or indirect.
- **What followed similar filings** (M2, high filings only), from `base_rates` in the detail API (ADR 0018): a Segmented toggle for the filter setting ("$5+ and $1M+ a day", the default, or "All stocks"); then for each high event, its label ("Insider open-market purchase", "8-K item 2.02 …"), a muted line saying which past filings the numbers are based on ("Based on past filings from companies in the same broad industry (SIC group 36, which includes …)" or "Based on past filings from all US-listed companies (fewer than 30 in this company's industry, so pooled)") and when they were calculated, and a table: After (1, 5, 20, 60 sessions) · Past filings · Average vs QQQ · Median vs QQQ · Beat QQQ · 95% range · vs SPY · Average cost. Returns are signed percentages, green above zero and red below; a range with too few months is a dash explained on hover; each header has an ⓘ in plain words. A muted footnote says the returns are after costs and minus QQQ's over the same days, so the market's own moves are taken out, and are what followed past filings, not a forecast, with a link to the glossary. A high filing with none yet says so in one muted line.
- Under the header, "Key financials:" links each ticker to its ticker page (M3). "Log a decision" (M4) is added by that milestone, not shown as a placeholder.

### Glossary (`/glossary`)

The text of `docs/glossary.md`, the one source for both GitHub and the app: plain-language meanings of every term the app uses, grouped as SEC and filings, Company financials, Alerts, Prices and markets, Research, and The app.

- **Two columns.** On the left, a topic list (200px) that stays in view while the page scrolls: each topic with its term count, the one being read marked with the accent rule; clicking jumps to it. On the right, the terms as dictionary rows separated by 1px rules: the term in bold, with what an abbreviation stands for in muted 12px text under it, in a 200px column; then the meaning, a muted **Example:** line, and **See also:** links to related terms.
- **Search** above both columns narrows them to terms whose name, expansion, meaning or example contains every word typed, and says "3 of 62 terms". Following a See also link clears the search, so the term it names is there.
- Each term and topic has an anchor (`/glossary#cik`, `/glossary#research`), so other pages can link to it.
- The file may only use the Markdown the page renders: headings, paragraphs, bold and code. Under a term, paragraphs starting `**Short for:**`, `**Example:**` and `**See also:**` (names separated by semicolons) become those fields. A test fails on anything else, or on a See also that names no term.
- No cards, category labels, telemetry or keyboard shortcuts.

### Ticker (`/watchlist/:symbol`, M3)

Opened from a ticker on Watchlist (a link once SEC has tied it to a company) or from "Key financials:" on Alert detail. The Watchlist tab stays selected.

Data: `GET /api/instruments/{symbol}/financials` (ADR 0020), from `xbrl_facts`.

- "← Watchlist", then the ticker as the page title, with a muted line: company name · filer type · CIK.
- **Key financials**, with a Segmented toggle: Quarters (default, the latest 8) or Fiscal years. The choice is in the address (`?basis=annual`), replacing rather than adding a history step. A foreign private issuer files only annual reports with XBRL, so it gets fiscal years and a muted line saying why, with no toggle.
- One table: metrics down the side (each with an ⓘ giving its definition), periods across, newest first. Each header is the filing's own fiscal label ("Q3 FY2027", "FY2026") over a muted "Ended Oct 26, 2026". Period columns are 140px; spare width stays empty on the right.
- Every number is a link to the filing it came from (for a worked-out value, the most recently filed source). Hovering shows the exact figure, how it was arrived at (as filed; a longer period minus the part before; worked out from several values) and each filed value used: concept, period, form and filing date. Numbers stay neutral apart from the link color: a loss shows a minus sign, not red.
- An empty cell is a muted dash, with why on hover: nothing filed for that period, or for a fourth quarter's EPS, that per-share figures can't be split by subtraction.
- A muted footnote: the latest 8 periods at most, newest first, as filed in XBRL, restatements replace earlier figures, click for the filing, hover for the figure; a link to the glossary's Company financials topic.
- 10-K changes are added by their own M3 step, not shown as a placeholder.

## Reference screenshots

`web/design/reference/` holds early mockups at 1440px wide. **Follow this file wherever a screenshot disagrees with it.**

| File | Keep | Ignore |
| --- | --- | --- |
| `alerts.png` | Table density, form tags, time column format | Uppercase monospace headers and monospace numbers (see Typography), three-level materiality, "Synthesized summary" column, the wrapped filter rows, the price tape, "LIVE" and latency badges, the shortcut bar and footer |
| `tasks.png` | Definitions table, runs table, status tags, the expanded row with error and JSON | `sync_watchlist_metrics`, "Execution pipeline steps" cards, run and node IDs, Celery, Redis and worker names, green inserted counts, the top bar style |
| `watchlist.png` | Add-ticker input with inline error, onboarding `Steps`, the benchmarks section | Company names for SPCX and ANT (SpaceX, Anthropic), "Section 01 /" labels, the capacity limit, the filer filter, "Refresh status", 5y backfill text, all telemetry |
| `signin.png` | Centred narrow form | The tagline, the "Enter ↵" hint, the shadow |
