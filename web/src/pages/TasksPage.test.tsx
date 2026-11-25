// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { TaskRunDetail, TaskRunSummary } from "../api/tasks";
import { installDomStubs } from "../test/dom";
import { TasksPage } from "./TasksPage";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ERROR = "ANT: CIK 0002130357 for ANT is not an operating company (entityType='other')";

const run: TaskRunSummary = {
  id: 7,
  task_id: "ingest_sec_filings",
  run_mode: "manual",
  status: "partial",
  created_at: "2026-11-25T05:24:00Z",
  started_at: "2026-11-25T05:24:01Z",
  completed_at: "2026-11-25T05:24:03Z",
  duration_seconds: 2,
  fetched_count: 3098,
  inserted_count: 3098,
  deduped_count: 0,
  error_message: ERROR
};

const detail: TaskRunDetail = {
  ...run,
  trace_id: "3cea9960-f81e-4065-9b91-4665b041e600",
  requested_by: "web:admin",
  tickers: [],
  as_of_date: null,
  force_refresh: false,
  meta: { dispatch_payload: {} }
};

function mockApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/tasks") return json([]);
    if (url.startsWith("/api/task-runs?")) return json({ items: [run], total: 1 });
    if (url === "/api/task-runs/7") return json(detail);
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("TasksPage run history", () => {
  it("is titled Run history", async () => {
    mockApi();
    render(<TasksPage />);

    expect(await screen.findByRole("heading", { name: "Run history" })).toBeTruthy();
  });

  it("expands a run when any part of its row is clicked", async () => {
    const fetchMock = mockApi();
    render(<TasksPage />);

    fireEvent.click(await screen.findByText("manual", { selector: "td" }));

    expect(await screen.findByText(detail.trace_id)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/task-runs/7")).toBe(true);
  });

  it("shows the full error in a tooltip on hover", async () => {
    mockApi();
    render(<TasksPage />);

    fireEvent.mouseEnter(await screen.findByText(ERROR));

    expect((await screen.findByRole("tooltip")).textContent).toContain(ERROR);
  });
});
