import { Table, Tooltip, Typography, type TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import {
  ACTION_LABEL,
  CALL_SIGN,
  callRight,
  dayLabel,
  dollars,
  percent,
  signedDollars,
  type JournalCombined,
  type JournalEntry,
  type Money,
  type MoneyPosition
} from "../api/journal";
import { EMPTY } from "./Timestamp";

/** Fewer daily returns than this and alpha and beta are not given (ADR 0027). */
export const MIN_DAYS = 20;

function Muted({ children }: { children: React.ReactNode }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

function Hover({ text, children }: { text: React.ReactNode; children: React.ReactNode }) {
  return (
    <Tooltip title={text} mouseEnterDelay={0.05}>
      <span className="cursor-help">{children}</span>
    </Tooltip>
  );
}

/** A signed percentage, green above zero and red below (DESIGN.md: gains and losses). */
export function Signed({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <Muted>{EMPTY}</Muted>;
  const tone = value > 0 ? "text-semantic-positive" : value < 0 ? "text-semantic-negative" : "";
  return <span className={tone}>{percent(value)}</span>;
}

function SignedMoney({ value }: { value: number | null }) {
  if (value === null) return <Muted>{EMPTY}</Muted>;
  const tone = value > 0 ? "text-semantic-positive" : value < 0 ? "text-semantic-negative" : "";
  return <span className={tone}>{signedDollars(value)}</span>;
}

const WAITING_HELP =
  "No close to measure from yet: an entry starts at the first daily close after it was logged, or on " +
  "its trade day once that day's prices are stored (after the close).";

/** The stock's return since the entry, with where it started and ended on hover. */
export function ReturnCell({ entry }: { entry: JournalEntry }) {
  const score = entry.score;
  if (!score) return <Muted>{EMPTY}</Muted>;
  if (score.status === "waiting")
    return (
      <Hover text={WAITING_HELP}>
        <Muted>Waiting</Muted>
      </Hover>
    );
  const start = score.start_basis === "trade" ? "your trade price" : "the close";
  const end = score.ends === "closed" ? "the Closed entry" : "the latest close";
  const lines = [
    `${entry.symbol} ${percent(score.stock_return ?? 0)}: from ${start} on ${dayLabel(score.start_date ?? "")} ` +
      `to ${end} on ${dayLabel(score.end_date ?? "")}, ${score.sessions} ${score.sessions === 1 ? "session" : "sessions"}.`,
    `Over the same days: QQQ ${score.qqq_return === null ? EMPTY : percent(score.qqq_return)}, ` +
      `SPY ${score.spy_return === null ? EMPTY : percent(score.spy_return)}.`
  ];
  if (score.price_check === "out_of_range")
    lines.push(
      "The trade price was outside that day's low–high range, so it is measured from the next close after it was logged."
    );
  return (
    <Hover
      text={
        <>
          {lines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </>
      }
    >
      <Signed value={score.stock_return} />
      {score.price_check === "out_of_range" && <Muted> *</Muted>}
    </Hover>
  );
}

/** Alpha, with the split of the return on hover; "Too early" under MIN_DAYS daily returns. */
export function AlphaCell({ entry }: { entry: JournalEntry }) {
  const score = entry.score;
  if (!score || score.status === "waiting") return <Muted>{EMPTY}</Muted>;
  if (score.fit !== "ok" || score.alpha === null)
    return (
      <Hover
        text={`Alpha and beta need ${MIN_DAYS} trading days to say much; this entry has ${score.days} so far.`}
      >
        <Muted>Too early</Muted>
      </Hover>
    );
  return (
    <Hover
      text={
        <>
          <div>{`Return ${percent(score.stock_return ?? 0)} = market part ${percent(score.market_part ?? 0)} + alpha ${percent(score.alpha)}.`}</div>
          <div>{`Market part: beta ${score.beta?.toFixed(2)} × QQQ ${percent(score.qqq_return ?? 0)}. Alpha is what QQQ's moves don't explain.`}</div>
          <div>{`r² ${score.r_squared?.toFixed(2)}: the share of daily moves the fit explains, over ${score.days} days.`}</div>
        </>
      }
    >
      <Signed value={score.alpha} />
    </Hover>
  );
}

export function BetaCell({ entry }: { entry: JournalEntry }) {
  const beta = entry.score?.beta;
  if (beta === null || beta === undefined) return <Muted>{EMPTY}</Muted>;
  return (
    <Hover text={`Moved about ${beta.toFixed(2)}% for each 1% move in QQQ over this entry's days.`}>
      {beta.toFixed(2)}
    </Hover>
  );
}

/** Whether the call was right beyond QQQ's moves, in the direction of the action. */
export function CallCell({ entry }: { entry: JournalEntry }) {
  const right = callRight(entry);
  if (right === null) return <Muted>{EMPTY}</Muted>;
  const wanted =
    CALL_SIGN[entry.action] > 0
      ? "above zero: the stock did better than QQQ's moves explain"
      : "below zero: the stock did worse than QQQ's moves explain";
  return (
    <Hover text={`${ACTION_LABEL[entry.action]} is right when alpha is ${wanted}.`}>
      <span className={right ? "text-semantic-positive" : "text-semantic-negative"}>
        {right ? "Right" : "Wrong"}
      </span>
    </Hover>
  );
}

/** One plain paragraph: how the calls have done together. */
export function RecordOfCalls({ combined }: { combined: JournalCombined }) {
  if (combined.scored === 0) return null;
  const wrong = combined.fitted - combined.calls_right;
  return (
    <div className="max-w-[880px]">
      {combined.fitted === 0 ? (
        <span>
          {`${combined.scored} ${combined.scored === 1 ? "entry is" : "entries are"} being measured, but none has ${MIN_DAYS} trading days yet, so there is no alpha or beta to judge the calls by.`}
        </span>
      ) : (
        <span>
          {`${combined.fitted} of ${combined.scored} measured entries have ${MIN_DAYS}+ trading days: `}
          <span className="text-semantic-positive">{`${combined.calls_right} right`}</span>
          {", "}
          <span className="text-semantic-negative">{`${wrong} wrong`}</span>
          {". Average alpha of the calls: "}
          <Signed value={combined.alpha_of_calls} />
          {"."}
          {combined.beta_of_holdings !== null &&
            ` Beta of what you held: ${combined.beta_of_holdings.toFixed(2)}, over ${combined.holdings_days} days.`}
          {combined.too_early > 0 &&
            ` ${combined.too_early} more ${combined.too_early === 1 ? "is" : "are"} too early to judge.`}
        </span>
      )}
      <div>
        <Muted>
          {
            "Each entry counts once, whatever its size. Alpha is the part of a stock's return QQQ's moves don't explain; for Trimmed, Closed and Pass a call is right when it is below zero. A few entries say little: read the counts first."
          }
        </Muted>
      </div>
    </div>
  );
}

const EXCLUDED: Record<NonNullable<MoneyPosition["excluded"]>, string> = {
  no_shares: "Left out: an entry has no share count",
  waiting: "Left out until its prices are stored"
};

function money(value: number | null) {
  return value === null ? <Muted>{EMPTY}</Muted> : dollars(value);
}

const moneyColumns: TableColumnsType<MoneyPosition> = [
  {
    title: "Ticker",
    dataIndex: "symbol",
    width: 80,
    render: (symbol: string) => (
      <Link to={`/journal?ticker=${encodeURIComponent(symbol)}`}>{symbol}</Link>
    )
  },
  {
    title: "Position",
    key: "position",
    width: 250,
    render: (_, p) => (
      <div>
        {p.start_date ? (
          `${p.status === "open" ? "Open" : "Closed"} · ${dayLabel(p.start_date)} – ${
            p.status === "open" ? "now" : dayLabel(p.end_date ?? p.start_date)
          }`
        ) : (
          <Muted>{p.status === "open" ? "Open" : "Closed"}</Muted>
        )}
        {p.excluded && (
          <div>
            <Muted>{EXCLUDED[p.excluded]}</Muted>
          </div>
        )}
      </div>
    )
  },
  { title: "Put in", dataIndex: "bought", align: "right", width: 110, render: money },
  { title: "Taken out", dataIndex: "sold", align: "right", width: 110, render: money },
  { title: "Value now", dataIndex: "value", align: "right", width: 110, render: money },
  {
    title: "Profit",
    dataIndex: "profit",
    align: "right",
    width: 110,
    render: (value: number | null) => <SignedMoney value={value} />
  },
  {
    title: "Same money in QQQ",
    dataIndex: "qqq_profit",
    align: "right",
    width: 160,
    render: (value: number | null) => <SignedMoney value={value} />
  },
  {
    title: "Difference",
    key: "difference",
    align: "right",
    width: 110,
    render: (_, p) =>
      p.excluded ? (
        <Muted>{EMPTY}</Muted>
      ) : (
        <SignedMoney value={(p.profit ?? 0) - (p.qqq_profit ?? 0)} />
      )
  }
];

/** Positions in dollars against the same dollars in QQQ (ADR 0028). */
export function MoneyView({ money: view }: { money: Money }) {
  if (view.positions.length === 0) return null;
  const counted = view.positions.length - view.excluded;
  return (
    <div className="flex flex-col gap-8">
      <Table<MoneyPosition>
        rowKey="opened_id"
        size="small"
        tableLayout="fixed"
        scroll={{ x: 1050 }}
        columns={moneyColumns}
        dataSource={view.positions}
        pagination={false}
        rowClassName={(p) => (p.excluded ? "text-text-muted" : "")}
        summary={() =>
          counted > 0 ? (
            <Table.Summary.Row>
              <Table.Summary.Cell index={0} colSpan={2}>
                <Typography.Text
                  strong
                >{`Total of ${counted} ${counted === 1 ? "position" : "positions"}`}</Typography.Text>
              </Table.Summary.Cell>
              <Table.Summary.Cell index={2} align="right">
                {dollars(view.bought)}
              </Table.Summary.Cell>
              <Table.Summary.Cell index={3} colSpan={2} />
              <Table.Summary.Cell index={5} align="right">
                <SignedMoney value={view.profit} />
              </Table.Summary.Cell>
              <Table.Summary.Cell index={6} align="right">
                <SignedMoney value={view.qqq_profit} />
              </Table.Summary.Cell>
              <Table.Summary.Cell index={7} align="right">
                <SignedMoney value={view.profit - view.qqq_profit} />
              </Table.Summary.Cell>
            </Table.Summary.Row>
          ) : null
        }
      />
      <Muted>
        {`From the share counts and prices you logged. "Same money in QQQ" puts each amount into QQQ at the same moment and takes out the same share of it when you trimmed or closed. Complete only if every trade is logged with its share count${
          view.excluded
            ? `: ${view.excluded} ${view.excluded === 1 ? "position is" : "positions are"} left out`
            : ""
        }. Dividends are not included.`}
      </Muted>
    </div>
  );
}
