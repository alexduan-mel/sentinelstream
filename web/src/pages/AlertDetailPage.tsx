import { useCallback, useEffect, useState } from "react";
import { Descriptions, Space, Table, Tag, Typography, type TableColumnsType } from "antd";
import { Link, useLocation, useParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  form4CodeLabel,
  formLabel,
  getFiling,
  itemLabel,
  purchaseLabel,
  type FilingDetail,
  type FilingIssuer,
  type Form4Transaction
} from "../api/filings";
import { Hint, MaterialityTag, Pushed } from "../components/AlertCells";
import { BaseRateSection } from "../components/BaseRates";
import { LoadError } from "../components/LoadError";
import { SectionTitle } from "../components/PageTitle";
import { EMPTY, Timestamp } from "../components/Timestamp";
import { formatDuration } from "../lib/time";

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const priceFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 4
});

function amount(value: number | null, format: Intl.NumberFormat = numberFormat) {
  return value === null ? <Muted>{EMPTY}</Muted> : format.format(value);
}

const issuerColumns: TableColumnsType<FilingIssuer> = [
  {
    title: "Ticker",
    dataIndex: "ticker",
    width: 90,
    render: (ticker: string | null, issuer) =>
      ticker ?? <span className="font-mono">{issuer.cik}</span>
  },
  {
    title: "Materiality",
    dataIndex: "materiality",
    width: 110,
    render: (level: FilingIssuer["materiality"]) =>
      level ? (
        <Tag className="sentinel-tag" color={level === "high" ? "warning" : "default"}>
          {level === "high" ? "High" : "Low"}
        </Tag>
      ) : (
        <Muted>{EMPTY}</Muted>
      )
  },
  {
    title: "Rule",
    dataIndex: "materiality_reason",
    render: (reason: string | null) => reason ?? <Muted>Not classified yet</Muted>
  },
  {
    title: "Seen as",
    dataIndex: "is_backfill",
    width: 110,
    render: (history: boolean) =>
      history ? (
        <Hint text="Loaded when this company was first polled or onboarded, so never pushed">
          <Muted>History</Muted>
        </Hint>
      ) : (
        "News"
      )
  },
  {
    title: "First seen",
    dataIndex: "first_seen_at",
    width: 200,
    render: (value: string) => <Timestamp value={value} />
  }
];

const transactionColumns: TableColumnsType<Form4Transaction> = [
  {
    title: "Reporting owner",
    key: "owner",
    render: (_, t) => (
      <Space orientation="vertical" size={0}>
        <span>{t.reporting_owners}</span>
        {t.owner_roles && <Muted>{t.owner_roles}</Muted>}
      </Space>
    )
  },
  {
    title: "Security",
    dataIndex: "security_title",
    render: (value: string | null) => value ?? <Muted>{EMPTY}</Muted>
  },
  {
    title: "Date",
    dataIndex: "transaction_date",
    width: 110,
    render: (value: string | null) => value ?? <Muted>{EMPTY}</Muted>
  },
  {
    title: "Code",
    dataIndex: "transaction_code",
    width: 260,
    render: (code: string) => form4CodeLabel(code)
  },
  {
    title: "Shares",
    key: "shares",
    align: "right",
    width: 130,
    render: (_, t) => (
      <span>
        {t.acquired_disposed === "D" ? "−" : t.acquired_disposed === "A" ? "+" : ""}
        {amount(t.shares)}
      </span>
    )
  },
  {
    title: "Price",
    dataIndex: "price_per_share",
    align: "right",
    width: 110,
    render: (value: number | null) => amount(value, priceFormat)
  },
  {
    title: "Owned after",
    dataIndex: "shares_owned_after",
    align: "right",
    width: 140,
    render: (value: number | null) => amount(value)
  },
  {
    title: "Held",
    dataIndex: "direct_indirect",
    width: 80,
    render: (value: Form4Transaction["direct_indirect"]) =>
      value === "D" ? "Direct" : value === "I" ? "Indirect" : <Muted>{EMPTY}</Muted>
  }
];

/** One filing: what it is, why it was or was not pushed, how fast, and its Form 4 rows (DESIGN.md). */
export function AlertDetailPage() {
  const { accessionNo = "" } = useParams();
  const location = useLocation();
  const [state, setState] = useState<{
    data: FilingDetail | null;
    loading: boolean;
    error: string | null;
  }>({ data: null, loading: true, error: null });

  const load = useCallback(async () => {
    setState({ data: null, loading: true, error: null });
    try {
      setState({ data: await getFiling(accessionNo), loading: false, error: null });
    } catch (error) {
      setState({ data: null, loading: false, error: errorMessage(error) });
    }
  }, [accessionNo]);

  useEffect(() => {
    void load();
  }, [load]);

  // Back to the list as it was: its filters travel in the address (see AlertsPage).
  const fromSearch = (location.state as { fromSearch?: string } | null)?.fromSearch ?? "";
  const back = <Link to={`/${fromSearch}`}>← Alerts</Link>;
  if (state.error) {
    return (
      <Space orientation="vertical" size={16} className="w-full">
        {back}
        <LoadError message={state.error} onRetry={() => void load()} />
      </Space>
    );
  }
  const filing = state.data;
  if (!filing) {
    return (
      <Space orientation="vertical" size={16} className="w-full">
        {back}
        <Muted>Loading…</Muted>
      </Space>
    );
  }

  const items = filing.items.map(itemLabel);
  const purchase = purchaseLabel(filing);
  if (purchase) items.push(purchase);
  const title = `${filing.tickers.join(", ") || `CIK ${filing.cik}`} · ${formLabel(filing.form_type)}`;

  return (
    <Space orientation="vertical" size={16} className="w-full">
      {back}

      <Space orientation="vertical" size={4}>
        <Space size={12} align="center">
          <Typography.Title level={1} className="!m-0 !text-page-title">
            {title}
          </Typography.Title>
          <MaterialityTag filing={filing} />
        </Space>
        {items.map((label) => (
          <div key={label}>{label}</div>
        ))}
        {filing.materiality === "high" && filing.materiality_reason && (
          <Muted>{`Flagged: ${filing.materiality_reason}`}</Muted>
        )}
      </Space>

      <Descriptions
        bordered
        size="small"
        column={2}
        items={[
          {
            key: "accepted",
            label: "Accepted by EDGAR",
            children: <Timestamp value={filing.accepted_at} />
          },
          {
            key: "pushed",
            label: "Pushed to Slack",
            children: <Pushed slack={filing.slack} />
          },
          {
            key: "seen",
            label: "First seen by the poller",
            children: <Timestamp value={filing.first_seen_at} />
          },
          {
            key: "detection",
            label: "Detection delay",
            children:
              filing.detection_delay_seconds === null ? (
                <Hint text="Loaded as history when the company was first polled or onboarded, so the gap says nothing about polling">
                  <Muted>History</Muted>
                </Hint>
              ) : (
                formatDuration(filing.detection_delay_seconds)
              )
          },
          {
            key: "latency",
            label: "Acceptance to Slack",
            children:
              filing.alert_latency_seconds === null ? (
                <Muted>{EMPTY}</Muted>
              ) : (
                formatDuration(filing.alert_latency_seconds)
              )
          },
          {
            key: "attempts",
            label: "Slack attempts",
            children: filing.slack_attempts ?? <Muted>{EMPTY}</Muted>
          },
          {
            key: "accession",
            label: "Accession number",
            children: (
              <Typography.Text className="font-mono" copyable>
                {filing.accession_no}
              </Typography.Text>
            )
          },
          {
            key: "cik",
            label: "Filer CIK",
            children: <span className="font-mono">{filing.cik}</span>
          },
          { key: "filed", label: "Filing date", children: filing.filed_date },
          {
            key: "report",
            label: "Period of report",
            children: filing.report_date ?? <Muted>{EMPTY}</Muted>
          },
          {
            key: "document",
            label: "Primary document",
            children: filing.primary_document_url ? (
              <a href={filing.primary_document_url} target="_blank" rel="noopener noreferrer">
                {filing.primary_document} ↗
              </a>
            ) : (
              <Muted>{EMPTY}</Muted>
            )
          },
          {
            key: "index",
            label: "Filing index",
            children: (
              <a href={filing.sec_url} target="_blank" rel="noopener noreferrer">
                SEC ↗
              </a>
            )
          }
        ]}
      />

      {filing.materiality === "high" && <BaseRateSection rates={filing.base_rates ?? []} />}

      <SectionTitle>Companies it is listed under</SectionTitle>
      <Table<FilingIssuer>
        rowKey="cik"
        size="small"
        tableLayout="fixed"
        columns={issuerColumns}
        dataSource={filing.issuers}
        pagination={false}
      />

      {filing.form4_transactions.length > 0 && (
        <>
          <SectionTitle>Form 4 transactions</SectionTitle>
          <Table<Form4Transaction>
            rowKey="line_no"
            size="small"
            tableLayout="fixed"
            columns={transactionColumns}
            dataSource={filing.form4_transactions}
            pagination={false}
          />
        </>
      )}
    </Space>
  );
}
