"""``summarize_10k_changes``: a few plain-language points on what changed in a 10-K section (ADR 0024).

For each stored year-over-year diff (ADR 0023) without a summary from the current ``PROMPT_VERSION``,
Claude reads the added, removed and changed paragraphs (changes in figures or dates only are left
out) and writes up to ``MAX_POINTS`` points, each naming the changes it rests on by number. The
answer must match a schema (``Summary``), and then every point is checked here before it is stored
(ground rule 6). A point is **rejected** when:

- it cites no change, or a change that was not in what Claude read;
- it states a number that none of its cited paragraphs contains (numbers come from filings, not
  from a model);
- it gives advice about the stock, such as "sell the shares" or "undervalued" (ground rule 7);
- it is empty or longer than ``MAX_POINT_CHARS``.

Accepted points are stored with their change numbers; each change carries its accession numbers,
item and paragraph numbers, so every point resolves to sections of the two 10-Ks. A summary whose
points were all rejected is stored as rejected, with nothing to show, and makes the run ``partial``.

``known_at`` is when the summary was written, not when the 10-K was accepted: research reading as of
an earlier date must not see it. The task calls the Claude API, not SEC, and can take a minute, so it
runs on the research queue and never holds up the SEC poll. ``ANTHROPIC_API_KEY`` must be set; the
job fails with a clear error otherwise (ground rule 5).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values
from pydantic import BaseModel, Field

from filings.tenk import SECTIONS
from filings.tenk_diff import heading_before
from orchestration.task_runs import RunResult, TaskRun

LOGGER = logging.getLogger(__name__)

TASK_ID = "summarize_10k_changes"
MODEL = "claude-opus-5-5"
# Raise when the prompt or the checks change, so stored summaries are written again.
PROMPT_VERSION = 1
MAX_POINTS = 6
MAX_POINT_CHARS = 400
MAX_PER_RUN = 4

# --- what Claude returns ---------------------------------------------------------------------------


class Point(BaseModel):
    text: str = Field(description="One plain-language sentence about what changed.")
    change_ids: list[int] = Field(description="The numbers of the changes this point rests on, e.g. [12, 15].")


class Summary(BaseModel):
    points: list[Point]


SYSTEM = f"""You summarize what changed in one section of a US company's annual report (Form 10-K) since the \
previous year's report, for a long-term investor who is new to finance.

You receive the section's changes, each with a number in square brackets: paragraphs ADDED this year, \
REMOVED since last year, and CHANGED (last year's and this year's wording). Write at most {MAX_POINTS} points, the \
most significant first. A significant change is a new or dropped risk, a new business, product, market or \
regulation, a changed explanation of results, or a new commitment. Skip wording edits that change nothing.

Rules:
- Each point is one sentence in plain words, under 300 characters, saying what is new or different.
- Each point lists in change_ids the numbers of every change it rests on. Never cite a number you were not given.
- Use only facts stated in the cited changes. Copy any figure, date or percentage exactly as it appears there, \
or leave it out.
- Describe; do not judge. Never say whether to buy, sell or hold, and never call the company or its stock good, \
bad, cheap or expensive.
- If nothing significant changed, return no points."""


# --- checking a point ------------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Advice about the stock, not the business: "Amazon sells more ads" is fine, "sell the stock" is not.
_TRADING = re.compile(
    r"\b(?:buy|sell|hold|own)(?:ing)?\s+(?:the\s+|its\s+)?(?:stock|shares?)\b|\brecommend\w*|"
    r"\b(?:bullish|bearish|undervalued|overvalued|outperform\w*|underperform\w*)\b|\bprice target|"
    r"\bgood investment",
    re.I,
)


@dataclass(frozen=True)
class ChangeText:
    change_no: int
    kind: str
    old_text: str | None
    new_text: str | None
    heading: str | None = None

    @property
    def texts(self) -> str:
        return " ".join(t for t in (self.old_text, self.new_text) if t)


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "").rstrip(".") for match in _NUMBER.finditer(text)}


def check_point(point: Point, changes: dict[int, ChangeText]) -> str | None:
    """Why the point is rejected, or None when it may be shown."""
    text = point.text.strip()
    if not text:
        return "empty"
    if len(text) > MAX_POINT_CHARS:
        return "too long"
    if not point.change_ids:
        return "no citation"
    unknown = sorted(set(point.change_ids) - set(changes))
    if unknown:
        return f"cites changes it was not given: {unknown}"
    cited = " ".join(changes[number].texts for number in point.change_ids)
    missing = sorted(_numbers(text) - _numbers(cited))
    if missing:
        return f"numbers not in the cited paragraphs: {missing}"
    if _TRADING.search(text):
        return "trading language"
    return None


# --- the prompt ------------------------------------------------------------------------------------


def changes_prompt(company: str, item: str, changes: Sequence[ChangeText]) -> str:
    lines = [f"{company}, Item {item} ({SECTIONS[item.lower()]}): {len(changes)} changes since last year's 10-K.", ""]
    for change in changes:
        where = f' under "{change.heading}"' if change.heading else ""
        if change.kind == "changed":
            lines += [
                f"[{change.change_no}] CHANGED{where}",
                f"Last year: {change.old_text}",
                f"This year: {change.new_text}",
            ]
        elif change.kind == "added":
            lines += [f"[{change.change_no}] ADDED{where}", change.new_text or ""]
        else:
            lines += [f"[{change.change_no}] REMOVED{where}", change.old_text or ""]
        lines.append("")
    return "\n".join(lines)


# --- calling Claude --------------------------------------------------------------------------------


class SummaryConfigurationError(RuntimeError):
    pass


@dataclass
class Written:
    points: list[Point]
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False


Writer = Callable[[str], Written]


def claude_writer() -> Writer:
    """Calls Claude with the changes and returns its schema-validated points."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise SummaryConfigurationError(
            "ANTHROPIC_API_KEY is not set; add it to Parameter Store (/sentinelstream/ANTHROPIC_API_KEY) and redeploy"
        )
    import anthropic  # imported here so the rest of the app never needs the SDK

    client = anthropic.Anthropic()

    def write(prompt: str) -> Written:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "high"},
            output_format=Summary,
            # On a policy decline, the API re-runs the request on a fallback model it picks.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        refused = response.stop_reason == "refusal"
        parsed = None if refused else response.parsed_output
        return Written(
            points=list(parsed.points) if parsed else [],
            model=response.model,
            request_id=getattr(response, "_request_id", None),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            refused=refused,
        )

    return write


# --- the task --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DueDiff:
    cik: str
    symbol: str
    item: str
    old_accession_no: str
    new_accession_no: str


def due_diffs(conn: Connection, *, tickers: list[str], force: bool) -> list[DueDiff]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.cik, MIN(i.symbol), d.item, d.old_accession_no, d.new_accession_no
            FROM filing_section_diffs d
            JOIN instruments i ON i.cik = d.cik AND i.role = 'watchlist' AND i.active
            WHERE (cardinality(%(tickers)s::text[]) = 0 OR i.symbol = ANY(%(tickers)s::text[]))
              AND (%(force)s OR NOT EXISTS (
                SELECT 1 FROM filing_change_summaries s
                WHERE s.new_accession_no = d.new_accession_no AND s.item = d.item
                  AND s.prompt_version = %(version)s
              ))
            GROUP BY d.cik, d.item, d.old_accession_no, d.new_accession_no, d.known_at
            ORDER BY d.known_at DESC, d.cik, d.item
            """,
            {"tickers": tickers, "force": force, "version": PROMPT_VERSION},
        )
        rows = cursor.fetchall()
    conn.commit()
    return [DueDiff(*row) for row in rows]


def load_changes(conn: Connection, diff: DueDiff) -> list[ChangeText]:
    """The diff's changes with their text, except those in figures or dates only."""

    def paragraphs(cursor, accession_no: str) -> list[str]:
        cursor.execute(
            "SELECT text FROM filing_paragraphs WHERE accession_no = %s AND item = %s ORDER BY paragraph_no",
            (accession_no, diff.item),
        )
        return [row[0] for row in cursor.fetchall()]

    with conn.cursor() as cursor:
        old, new = paragraphs(cursor, diff.old_accession_no), paragraphs(cursor, diff.new_accession_no)
        cursor.execute(
            "SELECT change_no, kind, old_paragraph_no, new_paragraph_no FROM filing_paragraph_changes "
            "WHERE new_accession_no = %s AND item = %s AND NOT numbers_only ORDER BY change_no",
            (diff.new_accession_no, diff.item),
        )
        rows = cursor.fetchall()
    conn.commit()
    changes = []
    for change_no, kind, old_no, new_no in rows:
        heading_no = heading_before(new, new_no) if new_no else heading_before(old, old_no)
        source = new if new_no else old
        changes.append(
            ChangeText(
                change_no=change_no,
                kind=kind,
                old_text=old[old_no - 1] if old_no else None,
                new_text=new[new_no - 1] if new_no else None,
                heading=source[heading_no - 1] if heading_no else None,
            )
        )
    return changes


@dataclass
class Outcome:
    accepted: list[Point] = field(default_factory=list)
    rejected: list[tuple[Point, str]] = field(default_factory=list)


def check(points: list[Point], changes: Sequence[ChangeText]) -> Outcome:
    by_number = {change.change_no: change for change in changes}
    outcome = Outcome()
    for point in points[:MAX_POINTS]:
        reason = check_point(point, by_number)
        if reason is None:
            outcome.accepted.append(Point(text=point.text.strip(), change_ids=sorted(set(point.change_ids))))
        else:
            outcome.rejected.append((point, reason))
    for point in points[MAX_POINTS:]:
        outcome.rejected.append((point, "beyond the point limit"))
    return outcome


def store_summary(
    conn: Connection,
    diff: DueDiff,
    *,
    status: str,
    outcome: Outcome,
    written: Written | None,
    changes_read: int,
    task_run_id: int,
    now: datetime,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM filing_change_summaries WHERE new_accession_no = %s AND item = %s",
            (diff.new_accession_no, diff.item),
        )
        cursor.execute(
            """
            INSERT INTO filing_change_summaries (new_accession_no, item, status, model, prompt_version,
                                                 changes_read, accepted_count, rejected_count, rejections,
                                                 request_id, input_tokens, output_tokens, known_at, task_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                diff.new_accession_no,
                diff.item,
                status,
                written.model if written else None,
                PROMPT_VERSION,
                changes_read,
                len(outcome.accepted),
                len(outcome.rejected),
                [f"{reason}: {point.text[:200]}" for point, reason in outcome.rejected],
                written.request_id if written else None,
                written.input_tokens if written else 0,
                written.output_tokens if written else 0,
                now,
                task_run_id,
            ),
        )
        if outcome.accepted:
            execute_values(
                cursor,
                "INSERT INTO filing_change_summary_points (new_accession_no, item, point_no, text, change_nos) "
                "VALUES %s",
                [
                    (diff.new_accession_no, diff.item, number, point.text, point.change_ids)
                    for number, point in enumerate(outcome.accepted, start=1)
                ],
                template="(%s, %s, %s, %s, %s::integer[])",
            )
    conn.commit()


def summarize_10k_changes(
    run: TaskRun, conn: Connection, *, writer: Writer | None = None, now: datetime | None = None
) -> RunResult:
    due = due_diffs(conn, tickers=run.tickers, force=run.force_refresh)
    if not due:
        return RunResult(status="succeeded", meta={"message": "nothing_due"})
    write = writer or claude_writer()
    batch = due[:MAX_PER_RUN]
    meta: dict[str, Any] = {"summaries": {}, "errors": [], "remaining": len(due) - len(batch)}
    problems: list[str] = []
    tokens = 0
    for diff in batch:
        label = f"{diff.symbol} {diff.new_accession_no} Item {diff.item}"
        changes = load_changes(conn, diff)
        if not changes:
            store_summary(
                conn, diff, status="no_changes", outcome=Outcome(), written=None, changes_read=0,
                task_run_id=run.id, now=now or datetime.now(UTC),
            )  # fmt: skip
            meta["summaries"][label] = {"status": "no_changes"}
            continue
        try:
            written = write(changes_prompt(diff.symbol, diff.item, changes))
        except Exception as exc:  # an API error leaves the diff due for the next run
            meta["errors"].append(f"{label}: {exc}")
            LOGGER.warning("tenk_summary_failed %s error=%s", label, exc)
            continue
        outcome = check(written.points, changes)
        if written.refused:
            status = "refused"
        elif outcome.accepted or not outcome.rejected:
            status = "ok"
        else:
            status = "rejected"
        store_summary(
            conn, diff, status=status, outcome=outcome, written=written, changes_read=len(changes),
            task_run_id=run.id, now=now or datetime.now(UTC),
        )  # fmt: skip
        tokens += written.input_tokens + written.output_tokens
        meta["summaries"][label] = {
            "status": status,
            "accepted": len(outcome.accepted),
            "rejected": [reason for _, reason in outcome.rejected],
            "model": written.model,
            "request_id": written.request_id,
        }
        if status != "ok":
            problems.append(f"{label}: summary {status}")
        LOGGER.info("tenk_summary_stored %s status=%s accepted=%d", label, status, len(outcome.accepted))
    meta["tokens"] = tokens
    problems = meta["errors"] + problems
    if not meta["summaries"]:
        status = "failed"
    elif problems:
        status = "partial"
    else:
        status = "succeeded"
    return RunResult(
        status=status,
        fetched_count=len(meta["summaries"]),
        inserted_count=sum(v.get("accepted", 0) for v in meta["summaries"].values()),
        error_message="; ".join(problems) or None,
        meta=meta,
    )
