# 0009: Slack notifier and the alert outbox

Status: accepted, 26 Nov 2026.

## Context

M1 pushes high filings ([ADR 0008](0008-materiality-rules-v0.md)) to Slack: one message per filing, with a link to it, deduplicated by accession number, and within 10 minutes of EDGAR acceptance. The webhook URL is a secret and lives in Parameter Store. Slack must sit behind an interface so email or Telegram can be added later. The notifier also has to avoid pushing stale highs: links classified as news in the days before it was switched on, and old filings classified late (after an outage, say, or a re-queue like migration 007).

## Decision

**Delivery runs as step 4 of `ingest_sec_filings`**, right after classification, so a high filing goes out in the same poll that finds it. A separate task would add a queue hop and a second schedule for no gain at this size. The poll closes its SEC client before sending, and a delivery failure makes the run `partial` (filings are still stored), with Slack's reason in the error.

**`alert_deliveries` is an outbox keyed on (accession number, channel).** A filing is queued when any of its links is high and news (`is_backfill = FALSE`). The key is the deduplication: one message per filing per channel, naming every issuer it is high for, however often the poller reruns. A row is `pending`, `delivered` (with `delivered_at`, so latency is `delivered_at - accepted_at`) or `skipped` (with `skip_reason`). A new channel would get its own rows, so it would not resend what Slack already had.

**Stale highs are never pushed.**

- Migration 008 marks every high news link classified before it as `skipped` ("classified before the Slack notifier was switched on"). On 26 Nov 2026 that was one ANT 8-K accepted the day before.
- A filing accepted more than 3 days before it is queued is recorded as `skipped`, not sent.
- A pending alert still undelivered 3 days after acceptance is given up as `skipped`, with its last error.

**Sending:** oldest acceptance first. The first failure stops the batch, and the rest stay pending for the next poll, five minutes later. The row is marked delivered straight after Slack answers `200 ok`. If the worker died in between, the next run would send the alert again: a rare duplicate is better than a lost alert. Slack's roughly one message a second per webhook is respected client-side, and 429 and 5xx are retried with backoff that honours `Retry-After` (ground rule 5).

**Interface:** `alerts.notifier.Notifier` has a `channel` name, `send(alert)` and `close()`. An `Alert` carries the accession number, form, acceptance time, SEC link and, for each high issuer, its ticker and the rule's reason. `alerts.slack.SlackNotifier` posts mrkdwn text with link unfurls off. The text holds only filing facts and the rule that matched (ground rule 7).

**The webhook URL** comes from `SLACK_WEBHOOK_URL`: on EC2, the SecureString `/sentinelstream/SLACK_WEBHOOK_URL`, which deploy.sh loads like every other parameter. It must start with `https://hooks.slack.com/`. It is never logged and is scrubbed from error text before that text reaches `task_runs`. The notifier is created only when an alert is pending, so the setting matters only when there is something to send. Without it, the poll ends `partial` with a clear error and the alert stays queued (ground rule 5: no silent success).

## Consequences

- The `alert_delivered` log line records each delivery's latency, and `alert_deliveries` holds the stored times that the last M1 box is checked from.
- Quiet hours are not handled in SS. Slack's own Do Not Disturb schedule holds notifications overnight; holding alerts here would break the 10-minute target, because EDGAR's filing hours can fall overnight in the reader's time zone.
- A filing that becomes high for a second issuer after its message went out is not sent again. With v0 rules this needs a co-registrant link that appears after delivery, which is rare.
