// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  callRight,
  dollars,
  percent,
  signedDollars,
  type JournalCombined,
  type JournalEntry,
  type JournalScore,
  type Money
} from "../api/journal";
import { installDomStubs } from "../test/dom";
import {
  AlphaCell,
  BetaCell,
  CallCell,
  MoneyView,
  RecordOfCalls,
  ReturnCell
} from "./JournalScores";

beforeAll(installDomStubs);
afterEach(cleanup);

const scored: JournalScore = {
  status: "scored",
  start_date: "2026-09-01",
  start_basis: "next_close",
  start_price: 100,
  price_check: null,
  end_date: "2026-10-13",
  end_price: 112,
  ends: "latest",
  closed_by: null,
  sessions: 30,
  stock_return: 0.12,
  qqq_return: 0.06,
  spy_return: 0.04,
  vs_qqq: 0.06,
  vs_spy: 0.08,
  fit: "ok",
  days: 30,
  beta: 1.5,
  r_squared: 0.64,
  market_part: 0.09,
  alpha: 0.03
};

function entry(action: JournalEntry["action"], score: JournalScore | null): JournalEntry {
  return {
    id: 1,
    symbol: "NVDA",
    name: "NVIDIA CORP",
    action,
    thesis: "t",
    source: null,
    original_thesis: "t",
    original_source: null,
    traded_on: null,
    price: null,
    shares: null,
    known_at: "2026-09-01T14:00:00Z",
    recorded_by: "admin",
    withdrawn: null,
    corrections: [],
    score
  };
}

async function hoverText(element: HTMLElement): Promise<string> {
  fireEvent.mouseEnter(element);
  const tooltip = await screen.findByRole("tooltip");
  return tooltip.textContent ?? "";
}

describe("formatting and calls", () => {
  it("formats percentages and dollars with true minus signs", () => {
    expect(percent(0.1234)).toBe("+12.3%");
    expect(percent(-0.023)).toBe("−2.3%");
    expect(percent(0)).toBe("0.0%");
    expect(dollars(1565.4)).toBe("$1,565");
    expect(signedDollars(-120)).toBe("−$120");
    expect(signedDollars(73)).toBe("+$73");
  });

  it("judges a call by its alpha in the direction of the action", () => {
    expect(callRight(entry("opened", scored))).toBe(true);
    expect(callRight(entry("pass", scored))).toBe(false); // it did well beyond QQQ: passing was wrong
    expect(callRight(entry("closed", { ...scored, alpha: -0.02 }))).toBe(true);
    expect(callRight(entry("opened", { ...scored, fit: "too_early", alpha: null }))).toBeNull();
    expect(callRight(entry("opened", null))).toBeNull();
  });
});

describe("score cells", () => {
  it("shows the return, with dates and the benchmarks on hover", async () => {
    render(<ReturnCell entry={entry("opened", scored)} />);
    const value = screen.getByText("+12.0%");
    expect(value.className).toContain("text-semantic-positive");
    const text = await hoverText(value);
    expect(text).toContain(
      "NVDA +12.0%: from the close on Sep 1, 2026 to the latest close on Oct 13, 2026, 30 sessions."
    );
    expect(text).toContain("QQQ +6.0%, SPY +4.0%");
  });

  it("flags a trade price that fell outside its day's range", async () => {
    render(<ReturnCell entry={entry("opened", { ...scored, price_check: "out_of_range" })} />);
    expect(await hoverText(screen.getByText("+12.0%"))).toContain(
      "outside that day's low–high range"
    );
  });

  it("says waiting before there is a close to start from", async () => {
    render(
      <ReturnCell entry={entry("pass", { ...scored, status: "waiting", stock_return: null })} />
    );
    expect(await hoverText(screen.getByText("Waiting"))).toContain(
      "first daily close after it was logged"
    );
  });

  it("splits the return into the market part and alpha on hover", async () => {
    render(<AlphaCell entry={entry("opened", scored)} />);
    const text = await hoverText(screen.getByText("+3.0%"));
    expect(text).toContain("Return +12.0% = market part +9.0% + alpha +3.0%.");
    expect(text).toContain("beta 1.50 × QQQ +6.0%");
    expect(text).toContain("r² 0.64");
  });

  it("says too early under 20 days, and shows beta and the call once fitted", async () => {
    const early = { ...scored, fit: "too_early" as const, days: 7, alpha: null, beta: null };
    render(<AlphaCell entry={entry("opened", early)} />);
    expect(await hoverText(screen.getByText("Too early"))).toContain("this entry has 7 so far");
    cleanup();

    render(
      <>
        <BetaCell entry={entry("opened", scored)} />
        <CallCell entry={entry("pass", scored)} />
      </>
    );
    expect(screen.getByText("1.50")).toBeTruthy();
    const wrong = screen.getByText("Wrong");
    expect(await hoverText(wrong)).toContain("Pass is right when alpha is below zero");
  });
});

const combined: JournalCombined = {
  scored: 12,
  fitted: 8,
  too_early: 4,
  alpha_of_calls: 0.021,
  calls_right: 5,
  beta_of_holdings: 1.42,
  holdings_days: 230
};

describe("RecordOfCalls", () => {
  it("puts the counts first, then the average alpha and the beta of holdings", () => {
    render(<RecordOfCalls combined={combined} />);
    expect(document.body.textContent).toContain(
      "8 of 12 measured entries have 20+ trading days: 5 right, 3 wrong. Average alpha of the calls: +2.1%. " +
        "Beta of what you held: 1.42, over 230 days. 4 more are too early to judge."
    );
  });

  it("says when nothing can be judged yet, and shows nothing with no scores", () => {
    render(<RecordOfCalls combined={{ ...combined, fitted: 0, scored: 2, calls_right: 0 }} />);
    expect(document.body.textContent).toContain(
      "2 entries are being measured, but none has 20 trading days yet"
    );
    cleanup();
    const { container } = render(<RecordOfCalls combined={{ ...combined, scored: 0 }} />);
    expect(container.textContent).toBe("");
  });
});

describe("MoneyView", () => {
  const view: Money = {
    positions: [
      {
        symbol: "NVDA",
        opened_id: 1,
        start_date: "2026-11-18",
        end_date: "2026-11-23",
        status: "open",
        excluded: null,
        bought: 1565,
        sold: 648,
        value: 990,
        profit: 73,
        qqq_profit: 25
      },
      {
        symbol: "AMZN",
        opened_id: 5,
        start_date: null,
        end_date: null,
        status: "closed",
        excluded: "no_shares",
        bought: null,
        sold: null,
        value: null,
        profit: null,
        qqq_profit: null
      }
    ],
    bought: 1565,
    profit: 73,
    qqq_profit: 25,
    excluded: 1
  };

  it("lists positions in dollars against QQQ, totals the complete ones and says what is left out", () => {
    render(
      <MemoryRouter>
        <MoneyView money={view} />
      </MemoryRouter>
    );
    const nvda = screen.getByRole("link", { name: "NVDA" }).closest("tr") as HTMLElement;
    expect(within(nvda).getByText("Open · Nov 18, 2026 – now")).toBeTruthy();
    expect(within(nvda).getByText("$648")).toBeTruthy();
    expect(within(nvda).getByText("+$48")).toBeTruthy(); // 73 - 25

    const amzn = screen.getByRole("link", { name: "AMZN" }).closest("tr") as HTMLElement;
    expect(within(amzn).getByText("Left out: an entry has no share count")).toBeTruthy();

    expect(screen.getByText("Total of 1 position")).toBeTruthy();
    expect(document.body.textContent).toContain("1 position is left out");
  });
});
