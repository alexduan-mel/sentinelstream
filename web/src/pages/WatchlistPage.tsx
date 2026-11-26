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

function OnboardingCell({ instrument }: { instrument: Instrument }) {
  const status = STATUS[instrument.onboarding_status];
  return (
    <Space orientation="vertical" size={2}>
      <Tag className="sentinel-tag" color={status.color}>
        {status.label}
      </Tag>
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
      render: (value: string) => <Typography.Text strong>{value}</Typography.Text>
    },
    {
      title: "Name",
      dataIndex: "name",
      ellipsis: true,
      render: (value: string | null) => value ?? <Muted>{EMPTY}</Muted>
    },
    {
      title: "Filer type",
      dataIndex: "filer_type",
      width: 130,
      render: (value: Instrument["filer_type"]) =>
        value ? FILER_TYPE_LABEL[value] : <Muted>{EMPTY}</Muted>
    },
    {
      title: "CIK",
      dataIndex: "cik",
      width: 120,
      render: (value: string | null) =>
        value ? <span className="font-mono">{value}</span> : <Muted>{EMPTY}</Muted>
    },
    {
      title: "Onboarding",
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
            <Tag className="sentinel-tag" color={STATUS[item.onboarding_status].color}>
              {STATUS[item.onboarding_status].label}
            </Tag>
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
