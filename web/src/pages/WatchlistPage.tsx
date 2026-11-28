import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Button,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType
} from "antd";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  FILER_TYPE_LABEL,
  addInstrument,
  isOnboarding,
  listInstruments,
  priceHistoryLabel,
  removeInstrument,
  retryOnboarding,
  type Instrument,
  type OnboardingStatus
} from "../api/instruments";
import { Hint } from "../components/AlertCells";
import { HelpTitle } from "../components/HelpTitle";
import { LoadError } from "../components/LoadError";
import { PageTitle, SectionTitle } from "../components/PageTitle";
import { EMPTY, Timestamp } from "../components/Timestamp";

/** While any ticker is pending or backfilling, the list refreshes this often. */
export const ONBOARDING_POLL_MS = 5_000;

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

const STATUS: Record<OnboardingStatus, { label: string; color: string }> = {
  pending: { label: "Pending", color: "default" },
  backfilling: { label: "Backfilling", color: "processing" },
  ready: { label: "Ready", color: "success" },
  failed: { label: "Failed", color: "error" }
};

/** What each status means for a tracked ticker, and what happens next. */
export const STATUS_HELP: Record<OnboardingStatus, string> = {
  pending:
    "Added and waiting for the setup job, which usually starts within seconds. Next: Backfilling.",
  backfilling:
    "Identified on SEC and already checked for new filings every 5 minutes. Its history is loading: " +
    "2 years of filings, daily prices and financial data, usually in under a minute. Next: Ready.",
  ready:
    "Fully set up. New filings are checked every 5 minutes, and prices update after each US market close.",
  failed:
    "Setup could not finish; the reason is shown below. Click Retry to try again, or Remove it."
};

/** A Backfilling ticker with an error stopped at a temporary problem; it does not move on by itself. */
export const STUCK_HELP =
  "Stopped at a temporary problem (shown below). It is retried at the next daily setup run, 19:00 ET, " +
  "or click Retry now. It is still checked for new filings meanwhile.";

/** Benchmarks are only priced, so their statuses mean less. */
export const BENCHMARK_HELP: Record<OnboardingStatus, string> = {
  pending: "Waiting for the setup job to load its prices. Next: Ready.",
  backfilling: "Loading its prices. Next: Ready.",
  ready: "Two years of daily prices loaded; updated after each US market close.",
  failed: "Its prices could not be loaded; the reason is on the Tasks page."
};

export const COLUMN_HELP = {
  cik:
    "SEC's permanent ID for the company (Central Index Key). Its filings are fetched by this number, " +
    "which never changes, even when a ticker is renamed or reused. Share classes such as GOOGL and " +
    "GOOG share one.",
  filerType:
    "Domestic companies file 10-K, 10-Q, 8-K and Form 4; their 8-Ks with items 1.01, 2.02, 4.02 or " +
    "5.02 are alerted. Foreign private issuers file 20-F and 6-K instead; 6-Ks are not alerted yet, " +
    "as they carry no item numbers. Insider purchases on Form 4 are alerted for both.",
  onboarding:
    "Setting a ticker up after it is added: Pending → Backfilling → Ready, or Failed if something " +
    "goes wrong. Hover a status for what it means and what happens next."
};

function StatusTag({ status, help }: { status: OnboardingStatus; help: string }) {
  return (
    <Hint text={help}>
      <Tag className="sentinel-tag" color={STATUS[status].color}>
        {STATUS[status].label}
      </Tag>
    </Hint>
  );
}

function OnboardingCell({ instrument }: { instrument: Instrument }) {
  const stuck = instrument.onboarding_status === "backfilling" && instrument.onboarding_error;
  return (
    <Space orientation="vertical" size={2}>
      <StatusTag
        status={instrument.onboarding_status}
        help={stuck ? STUCK_HELP : STATUS_HELP[instrument.onboarding_status]}
      />
      {instrument.onboarding_error && (
        <Tooltip title={instrument.onboarding_error} mouseEnterDelay={0.05}>
          <Typography.Text
            type={instrument.onboarding_status === "failed" ? "danger" : "warning"}
            className="block max-w-[260px] cursor-help truncate !text-secondary"
          >
            {instrument.onboarding_error}
          </Typography.Text>
        </Tooltip>
      )}
    </Space>
  );
}

function LastFiling({ filing }: { filing: Instrument["last_filing"] }) {
  if (!filing) return <Muted>{EMPTY}</Muted>;
  return (
    <span className="whitespace-nowrap">
      {filing.form_type === "4" ? "Form 4" : filing.form_type} ·{" "}
      <Timestamp value={filing.accepted_at} />
    </span>
  );
}

/** Watchlist tickers with their onboarding status, and the fixed benchmarks. */
export function WatchlistPage() {
  const [instruments, setInstruments] = useState<Loadable<Instrument[]>>({
    data: null,
    loading: true,
    error: null
  });
  const [symbol, setSymbol] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setInstruments((current) => ({ ...current, loading: true, error: null }));
    try {
      setInstruments({ data: await listInstruments(), loading: false, error: null });
    } catch (error) {
      setInstruments((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const polling = instruments.data?.some(isOnboarding) ?? false;
  useEffect(() => {
    if (!polling) return undefined;
    const timer = window.setInterval(() => void load(true), ONBOARDING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, load]);

  const onAdd = async (event: FormEvent) => {
    event.preventDefault();
    const value = symbol.trim().toUpperCase();
    if (!value) return;
    setAdding(true);
    setAddError(null);
    try {
      await addInstrument(value);
      setSymbol("");
      await load(true);
    } catch (error) {
      setAddError(errorMessage(error));
    } finally {
      setAdding(false);
    }
  };

  const act = async (target: string, action: (value: string) => Promise<unknown>) => {
    setBusy(target);
    setActionError(null);
    try {
      await action(target);
      await load(true);
    } catch (error) {
      setActionError(`${target}: ${errorMessage(error)}`);
    } finally {
      setBusy(null);
    }
  };

  const tracked = instruments.data?.filter((item) => item.role === "watchlist") ?? [];
  const benchmarks = instruments.data?.filter((item) => item.role === "benchmark") ?? [];

  const trackedColumns: TableColumnsType<Instrument> = [
    {
      title: "Ticker",
      dataIndex: "symbol",
      width: 90,
      // Once SEC has tied the ticker to a company, it opens the ticker page.
      render: (value: string, item) =>
        item.cik ? (
          <Link to={`/watchlist/${encodeURIComponent(value)}`} className="font-semibold">
            {value}
          </Link>
        ) : (
          <Typography.Text strong>{value}</Typography.Text>
        )
    },
    {
      title: "Name",
      dataIndex: "name",
      ellipsis: true,
      render: (value: string | null) => value ?? <Muted>{EMPTY}</Muted>
    },
    {
      title: <HelpTitle title="Filer type" help={COLUMN_HELP.filerType} />,
      dataIndex: "filer_type",
      width: 140,
      render: (value: Instrument["filer_type"]) =>
        value ? FILER_TYPE_LABEL[value] : <Muted>{EMPTY}</Muted>
    },
    {
      title: <HelpTitle title="CIK" help={COLUMN_HELP.cik} />,
      dataIndex: "cik",
      width: 120,
      render: (value: string | null) =>
        value ? <span className="font-mono">{value}</span> : <Muted>{EMPTY}</Muted>
    },
    {
      title: <HelpTitle title="Onboarding" help={COLUMN_HELP.onboarding} />,
      key: "onboarding",
      width: 280,
      render: (_, item) => <OnboardingCell instrument={item} />
    },
    {
      title: "Last filing",
      key: "last_filing",
      width: 220,
      render: (_, item) => <LastFiling filing={item.last_filing} />
    },
    {
      title: "",
      key: "actions",
      align: "right",
      width: 150,
      render: (_, item) => (
        <Space size={4}>
          {(item.onboarding_status === "failed" || item.onboarding_error) && (
            <Tooltip title="Look the symbol up on SEC again and resume loading its history">
              <Button
                size="small"
                type="text"
                loading={busy === item.symbol}
                disabled={busy !== null && busy !== item.symbol}
                onClick={() => void act(item.symbol, retryOnboarding)}
              >
                Retry
              </Button>
            </Tooltip>
          )}
          <Popconfirm
            title={`Remove ${item.symbol}?`}
            description="It stops being polled. Its filings, prices and alerts are kept."
            okText="Remove"
            okButtonProps={{ danger: true }}
            cancelText="Cancel"
            onConfirm={() => act(item.symbol, removeInstrument)}
          >
            <Button size="small" type="text" disabled={busy !== null}>
              Remove
            </Button>
          </Popconfirm>
        </Space>
      )
    }
  ];

  const benchmarkColumns: TableColumnsType<Instrument> = [
    {
      title: "Ticker",
      dataIndex: "symbol",
      width: 90,
      render: (value: string) => <Typography.Text strong>{value}</Typography.Text>
    },
    {
      title: "Name",
      dataIndex: "name",
      render: (value: string | null) => value ?? <Muted>{EMPTY}</Muted>
    },
    {
      title: "Price history",
      key: "prices",
      render: (_, item) => {
        const history = priceHistoryLabel(item.prices);
        return (
          <Space size={8}>
            <StatusTag
              status={item.onboarding_status}
              help={BENCHMARK_HELP[item.onboarding_status]}
            />
            {history ? <span>{history}</span> : <Muted>No bars yet</Muted>}
          </Space>
        );
      }
    }
  ];

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <PageTitle title="Watchlist" count={instruments.data ? tracked.length : undefined} />

      <form onSubmit={(event) => void onAdd(event)} className="flex flex-wrap items-center gap-8">
        <Input
          aria-label="Ticker to add"
          placeholder="Ticker, e.g. MSFT"
          className="!w-[200px]"
          maxLength={10}
          value={symbol}
          status={addError ? "error" : undefined}
          onChange={(event) => {
            setSymbol(event.target.value.toUpperCase());
            setAddError(null);
          }}
        />
        <Button type="primary" htmlType="submit" loading={adding} disabled={!symbol.trim()}>
          Add ticker
        </Button>
        {addError && <Typography.Text type="danger">{addError}</Typography.Text>}
      </form>

      {instruments.error ? (
        <LoadError message={instruments.error} onRetry={() => void load()} />
      ) : (
        <>
          <SectionTitle>Tracked</SectionTitle>
          {actionError && <Typography.Text type="danger">{actionError}</Typography.Text>}
          <Table<Instrument>
            rowKey="symbol"
            size="small"
            tableLayout="fixed"
            columns={trackedColumns}
            dataSource={tracked}
            loading={instruments.loading}
            pagination={false}
            locale={{ emptyText: <Muted>No tickers yet. Add one above.</Muted> }}
          />
          <SectionTitle>Benchmarks</SectionTitle>
          <Table<Instrument>
            rowKey="symbol"
            size="small"
            tableLayout="fixed"
            columns={benchmarkColumns}
            dataSource={benchmarks}
            loading={instruments.loading}
            pagination={false}
            locale={{ emptyText: <Muted>No benchmarks</Muted> }}
          />
        </>
      )}
    </Space>
  );
}
