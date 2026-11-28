"""Cited summaries of 10-K changes: the checks alone, then the task against Postgres with a fake writer.

No test calls the Claude API.
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from filings.change_summaries import (
    PROMPT_VERSION,
    ChangeText,
    Point,
    RetryLater,
    SummaryConfigurationError,
    Written,
    changes_prompt,
    check_point,
    claude_writer,
    summarize_10k_changes,
)
from tests.test_onboarding import _count, _run
from tests.test_tenk_diff import _diff, _extracted
from tests.test_tenk_sections import TEN_K_2026

# --- the checks ------------------------------------------------------------------------------------

CHANGES = {
    1: ChangeText(1, "removed", "The COVID-19 pandemic could reduce demand for our products.", None, "Risks"),
    3: ChangeText(3, "added", None, "New export licensing requirements now cover more of our data center products."),
    4: ChangeText(4, "changed", "We had 29,600 employees.", "We had 36,000 employees in 38 countries.", "People"),
}


@pytest.mark.parametrize(
    ("point", "reason"),
    [
        (Point(text="Export licences now cover more data center products.", change_ids=[3]), None),
        (Point(text="Headcount rose to 36,000 from 29,600.", change_ids=[4]), None),
        (Point(text="The COVID-19 risk was dropped.", change_ids=[1]), None),  # "19" is in the cited text
        (Point(text="Export rules widened.", change_ids=[]), "no citation"),
        (Point(text="Export rules widened.", change_ids=[3, 9]), "cites changes it was not given: [9]"),
        (Point(text="Exports may cut revenue by $5 billion.", change_ids=[3]), "numbers not in the cited paragraphs"),
        (Point(text="Headcount rose to 36,000.", change_ids=[3]), "numbers not in the cited paragraphs"),
        (Point(text="Investors should sell the stock after the export rules.", change_ids=[3]), "trading language"),
        (Point(text="The shares look undervalued.", change_ids=[3]), "trading language"),
        (Point(text="   ", change_ids=[3]), "empty"),
        (Point(text="x" * 401, change_ids=[3]), "too long"),
    ],
)
def test_a_point_is_shown_only_when_every_check_passes(point: Point, reason: str | None) -> None:
    result = check_point(point, CHANGES)
    if reason is None:
        assert result is None
    else:
        assert result is not None and result.startswith(reason)


def test_selling_products_is_not_trading_language() -> None:
    point = Point(text="New export licensing requirements now cover more data center products we sell.", change_ids=[3])
    assert check_point(point, CHANGES) is None


def test_the_prompt_numbers_each_change() -> None:
    prompt = changes_prompt("NVDA", "1A", list(CHANGES.values()))

    assert prompt.startswith("NVDA, Item 1A (Risk factors): 3 changes since last year's 10-K.")
    assert '[1] REMOVED under "Risks"\nThe COVID-19 pandemic' in prompt
    assert "[3] ADDED\nNew export licensing" in prompt
    assert '[4] CHANGED under "People"\nLast year: We had 29,600 employees.\nThis year: We had 36,000' in prompt


# --- the task --------------------------------------------------------------------------------------


class FakeWriter:
    def __init__(self, *responses: Written | Exception) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> Written:
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _summarize(conn, writer, *, force: bool = False):
    return summarize_10k_changes(_run(conn, task_id="summarize_10k_changes", force_refresh=force), conn, writer=writer)


def _prepared(conn) -> None:
    _extracted(conn)
    _diff(conn)


def _summaries(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT item, status, model, prompt_version, changes_read, accepted_count, rejected_count, rejections "
            "FROM filing_change_summaries ORDER BY item"
        )
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def _points(conn) -> list[tuple]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT point_no, text, change_nos FROM filing_change_summary_points ORDER BY point_no")
        rows = cursor.fetchall()
    conn.rollback()
    return rows


def test_checked_points_are_stored_with_their_citations(connect_app_db) -> None:
    conn = connect_app_db()
    _prepared(conn)
    writer = FakeWriter(
        Written(
            points=[
                Point(text="NVIDIA added a risk about export licences covering more of its products.", change_ids=[3]),
                Point(text="It dropped the COVID-19 pandemic risk.", change_ids=[1]),
                Point(text="Revenue rose to $130.5 billion.", change_ids=[2]),  # figures-only: not given
                Point(text="Exports could cost $9 billion.", change_ids=[3]),
            ],
            model="claude-opus-5-5",
            request_id="req_1",
            input_tokens=900,
            output_tokens=120,
        )
    )

    result = _summarize(conn, writer)

    # One call: Item 1A. Item 7 has no change, so Claude is not asked.
    [prompt] = writer.prompts
    assert "[1] REMOVED" in prompt and "[3] ADDED" in prompt
    assert "[2]" not in prompt  # the change in figures only is left out
    assert result.status == "succeeded"
    assert result.inserted_count == 2
    assert _summaries(conn) == [
        (
            "1A", "ok", "claude-opus-5-5", PROMPT_VERSION, 2, 2, 2,
            [
                "cites changes it was not given: [2]: Revenue rose to $130.5 billion.",
                "numbers not in the cited paragraphs: ['9']: Exports could cost $9 billion.",
            ],
        ),
        ("7", "no_changes", None, PROMPT_VERSION, 0, 0, 0, []),
    ]  # fmt: skip
    assert _points(conn) == [
        (1, "NVIDIA added a risk about export licences covering more of its products.", [3]),
        (2, "It dropped the COVID-19 pandemic risk.", [1]),
    ]
    assert _summarize(conn, FakeWriter()).meta == {"message": "nothing_due"}


def test_all_points_rejected_or_refused_shows_nothing(connect_app_db) -> None:
    conn = connect_app_db()
    _prepared(conn)

    rejected = _summarize(conn, FakeWriter(Written(points=[Point(text="Buy the stock.", change_ids=[3])], model="m")))
    assert rejected.status == "partial"
    assert rejected.error_message == f"NVDA {TEN_K_2026} Item 1A: summary rejected"
    assert _summaries(conn)[0][:2] == ("1A", "rejected")
    assert _points(conn) == []

    _summarize(conn, FakeWriter(Written(points=[], model="m", refused=True)), force=True)
    assert _summaries(conn)[0][:2] == ("1A", "refused")


def test_a_free_failure_leaves_the_diff_due(connect_app_db) -> None:
    conn = connect_app_db()
    _prepared(conn)

    failed = _summarize(conn, FakeWriter(RetryLater("overloaded")))
    assert failed.status == "partial"  # Item 7 was still recorded as having no changes
    assert failed.error_message == f"NVDA {TEN_K_2026} Item 1A: overloaded"
    assert _count(conn, "SELECT COUNT(*) FROM filing_change_summaries WHERE item = '1A'") == 0

    ok = _summarize(conn, FakeWriter(Written(points=[], model="m")))
    assert list(ok.meta["summaries"]) == [f"NVDA {TEN_K_2026} Item 1A"]


def test_a_missing_api_key_fails_the_run_clearly(connect_app_db, monkeypatch) -> None:
    conn = connect_app_db()
    _prepared(conn)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(SummaryConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        _summarize(conn, None)


def test_a_new_diff_is_summarized_again(connect_app_db) -> None:
    conn = connect_app_db()
    _prepared(conn)
    _summarize(conn, FakeWriter(Written(points=[], model="m")))

    _diff(conn, force=True)  # replaces the diff, and with it the summaries

    assert _count(conn, "SELECT COUNT(*) FROM filing_change_summaries") == 0
    assert len(_summarize(conn, FakeWriter(Written(points=[], model="m"))).meta["summaries"]) == 2


def test_a_failure_that_may_have_been_billed_is_stored_and_not_retried(connect_app_db) -> None:
    conn = connect_app_db()
    _prepared(conn)

    result = _summarize(conn, FakeWriter(ValueError("the answer was cut off at max_tokens")))

    assert result.status == "partial"
    assert result.error_message == f"NVDA {TEN_K_2026} Item 1A: summary failed (ValueError)"
    [(item, status, *_rest, rejections)] = [row for row in _summaries(conn) if row[0] == "1A"]
    assert (item, status) == ("1A", "failed")
    assert rejections == ["ValueError: the answer was cut off at max_tokens"]
    assert _summarize(conn, FakeWriter()).meta == {"message": "nothing_due"}  # no hourly retry

    retried = _summarize(conn, FakeWriter(Written(points=[], model="m")), force=True)
    assert retried.meta["summaries"][f"NVDA {TEN_K_2026} Item 1A"]["status"] == "ok"


# --- the real SDK client against canned HTTP answers -----------------------------------------------


def _writer(monkeypatch, status: int, body: dict):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    transport = httpx2.MockTransport(lambda request: httpx2.Response(status, json=body))
    client = anthropic.Anthropic(
        api_key="test-key", max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=transport)
    )
    return claude_writer(client)


def _message(stop_reason: str, text: str) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 60},
    }  # fmt: skip


def _error(kind: str, message: str) -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


def test_the_sdk_answer_becomes_points(monkeypatch) -> None:
    answer = {"points": [{"text": "A new export risk.", "change_ids": [3]}]}
    written = _writer(monkeypatch, 200, _message("end_turn", json.dumps(answer)))("prompt")

    assert written.points == [Point(text="A new export risk.", change_ids=[3])]
    assert (written.model, written.input_tokens, written.output_tokens) == ("claude-opus-5-5", 900, 60)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (529, _error("overloaded_error", "Overloaded")),
        (500, _error("api_error", "Internal server error")),
        (429, _error("rate_limit_error", "Monthly spend cap reached")),
        (400, _error("invalid_request_error", "You have reached your specified API usage limits.")),
    ],
)
def test_failures_that_cost_nothing_are_retried_later(monkeypatch, status: int, body: dict) -> None:
    with pytest.raises(RetryLater):
        _writer(monkeypatch, status, body)("prompt")


def test_other_failures_are_not_retried(monkeypatch) -> None:
    with pytest.raises(anthropic.BadRequestError):
        _writer(monkeypatch, 400, _error("invalid_request_error", "messages: too long"))("prompt")
    # An answer cut off mid-way was billed: whatever fails on it (the SDK's schema check here) is
    # not a RetryLater, so the task stores it as failed instead of paying for it again every hour.
    with pytest.raises(Exception) as cut_off:
        _writer(monkeypatch, 200, _message("max_tokens", '{"points": [{"text": "A new'))("prompt")
    assert not isinstance(cut_off.value, RetryLater)
