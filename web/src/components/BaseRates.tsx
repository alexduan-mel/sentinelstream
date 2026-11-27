import { useState } from "react";
import { Segmented, Space, Table, Typography, type TableColumnsType } from "antd";
import { Link } from "react-router-dom";

import {
  BASE_RATE_HORIZONS,
  type BaseRate,
  type BaseRateStats,
  type BaseRateVariant
} from "../api/filings";
import { Hint } from "./AlertCells";
import { HelpTitle } from "./HelpTitle";
import { SectionTitle } from "./PageTitle";
import { EMPTY, Timestamp } from "./Timestamp";

const signed = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero"
});
const share = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 0 });
const count = new Intl.NumberFormat("en-US");

/** A return as a signed percentage; DESIGN.md: green for gains, red for losses. */
export function Return({ value }: { value: number | null }) {
  if (value === null) return <Typography.Text type="secondary">{EMPTY}</Typography.Text>;
  const text = signed.format(value);
  const color = value > 0 ? "text-semantic-positive" : value < 0 ? "text-semantic-negative" : "";
  return <span className={color}>{text}</span>;
}

type Row = { horizon: string; stats: BaseRateStats | null };

const HELP = {
  events:
    "How many past filings of this kind were followed for this many trading sessions. Several insiders " +
    "buying on the same day count once.",
  average:
    "The stock's return over the next sessions, bought at the first close after the filing was public, " +
    "after trading costs, minus QQQ's return over the same days. +1.0% means it did 1 point better than QQQ.",
  median:
    "The middle result: half did better than this, half worse. Less swayed by a few extreme moves.",
  beat: "Share of past filings after which the stock did better than QQQ, after costs.",
  range:
    "95% confidence range for the average. Filings in the same month move together, so it is worked out " +
    "by resampling whole months. A range that spans zero means history can't tell this apart from QQQ.",
  spy: "The same average, compared with SPY (the S&P 500) instead of QQQ.",
  cost:
    "Average round-trip trading cost taken off: 0.10% a trade, or half the stock's estimated buy-sell " +
    "gap when that is wider."
};

const columns: TableColumnsType<Row> = [
  {
    title: "After",
    dataIndex: "horizon",
    width: 110,
    render: (horizon: string) => `${horizon} ${horizon === "1" ? "session" : "sessions"}`
  },
  {
    title: <HelpTitle title="Past filings" help={HELP.events} />,
    key: "n",
    align: "right",
    render: (_, row) => (row.stats ? count.format(row.stats.n) : EMPTY)
  },
  {
    title: <HelpTitle title="Average vs QQQ" help={HELP.average} />,
    key: "mean",
    align: "right",
    render: (_, row) => <Return value={row.stats?.mean ?? null} />
  },
  {
    title: <HelpTitle title="Median vs QQQ" help={HELP.median} />,
    key: "median",
    align: "right",
    render: (_, row) => <Return value={row.stats?.median ?? null} />
  },
  {
    title: <HelpTitle title="Beat QQQ" help={HELP.beat} />,
    key: "hit_rate",
    align: "right",
    render: (_, row) => (row.stats ? share.format(row.stats.hit_rate) : EMPTY)
  },
  {
    title: <HelpTitle title="95% range" help={HELP.range} />,
    key: "range",
    align: "right",
    render: (_, row) => {
      if (!row.stats) return EMPTY;
      if (row.stats.ci_low === null || row.stats.ci_high === null) {
        return (
          <Hint text={`Needs at least six months of filings; these span ${row.stats.months}.`}>
            <Typography.Text type="secondary">{EMPTY}</Typography.Text>
          </Hint>
        );
      }
      return (
        <span className="whitespace-nowrap">
          <Return value={row.stats.ci_low} /> to <Return value={row.stats.ci_high} />
        </span>
      );
    }
  },
  {
    title: <HelpTitle title="vs SPY" help={HELP.spy} />,
    key: "spy",
    align: "right",
    render: (_, row) => <Return value={row.stats?.mean_excess_vs_reference ?? null} />
  },
  {
    title: <HelpTitle title="Average cost" help={HELP.cost} />,
    key: "cost",
    align: "right",
    render: (_, row) =>
      row.stats ? (
        <Typography.Text type="secondary">{signed.format(-row.stats.mean_cost)}</Typography.Text>
      ) : (
        EMPTY
      )
  }
];

const VARIANTS: { label: string; value: BaseRateVariant; help: string }[] = [
  {
    label: "$5+ and $1M+ a day",
    value: "main",
    help:
      "Only stocks that closed at $5 or more and traded at least $1M a day over the 20 sessions " +
      "before the filing: closer to what could really be traded."
  },
  { label: "All stocks", value: "none", help: "Every stock that traded on the entry day." }
];

/**
 * DESIGN.md, Alert detail (M2): what followed past filings like this one. Shown for high filings only.
 * History, not a forecast: the text never says what to do (ground rule 7).
 */
export function BaseRateSection({ rates }: { rates: BaseRate[] }) {
  const [variant, setVariant] = useState<BaseRateVariant>("main");
  return (
    <>
      <SectionTitle>What followed similar filings</SectionTitle>
      {rates.length === 0 ? (
        <Typography.Text type="secondary">
          No base rate yet. It is calculated each morning at 06:00 ET from past filings of this
          kind.
        </Typography.Text>
      ) : (
        <Space direction="vertical" size={16} className="w-full">
          <Segmented<BaseRateVariant>
            size="small"
            value={variant}
            onChange={setVariant}
            options={VARIANTS.map((v) => ({
              value: v.value,
              label: <Hint text={v.help}>{v.label}</Hint>
            }))}
          />
          {rates.map((rate) => (
            <Space
              key={`${rate.event_kind}:${rate.event_key}`}
              direction="vertical"
              size={4}
              className="w-full"
            >
              <Typography.Text strong>{rate.label}</Typography.Text>
              <Typography.Text type="secondary">
                {rate.scope_label} · calculated <Timestamp value={rate.as_of} />
              </Typography.Text>
              <Table<Row>
                rowKey="horizon"
                size="small"
                tableLayout="fixed"
                columns={columns}
                dataSource={BASE_RATE_HORIZONS.map((horizon) => ({
                  horizon,
                  stats: rate.variants[variant]?.[horizon] ?? null
                }))}
                pagination={false}
              />
            </Space>
          ))}
          <Typography.Text type="secondary">
            Returns after trading costs, minus what QQQ did over the same trading days, so the
            market&apos;s own moves are taken out. This is what followed past filings of this kind,
            not a forecast. <Link to="/glossary#base-rate">What a base rate is</Link>
          </Typography.Text>
        </Space>
      )}
    </>
  );
}
