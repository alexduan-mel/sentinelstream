import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  DatePicker,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  type TableColumnsType
} from "antd";

import { errorMessage } from "../api/client";
import {
  getFilingFacets,
  itemLabel,
  listFilings,
  pollLine,
  purchaseLabel,
  SEC_POLL_TASK,
  type Filing,
  type FilingFacets,
  type FilingItem,
  type FilingPage,
  type Materiality
} from "../api/filings";
import { listTasks, type TaskDefinition } from "../api/tasks";
import { MaterialityTag, Pushed } from "../components/AlertCells";
import { LoadError } from "../components/LoadError";
import { PageTitle } from "../components/PageTitle";
import { EMPTY, Timestamp } from "../components/Timestamp";
import { zoneLabel } from "../lib/time";
import { useTimeZone } from "../lib/timeZone";

export const FILINGS_PAGE_SIZE = 50;
/** How often the poll line re-reads the SEC poller's schedule. */
export const POLL_LINE_REFRESH_MS = 60_000;

type MaterialityFilter = "all" | Materiality;

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

function Items({ filing }: { filing: Filing }) {
  const labels = filing.items.map((item: FilingItem) => itemLabel(item));
  const purchase = purchaseLabel(filing);
  if (purchase) labels.push(purchase);
  if (labels.length === 0) return null;
  return (
    <>
      {labels.map((label) => (
        <div key={label} className="truncate" title={label}>
          {label}
        </div>
      ))}
    </>
  );
}

const columns: TableColumnsType<Filing> = [
  {
    title: "Accepted",
    dataIndex: "accepted_at",
    width: 180,
    render: (value: string) => <Timestamp value={value} />
  },
  {
    title: "Ticker",
    dataIndex: "tickers",
    width: 90,
    render: (tickers: string[], filing) =>
      tickers.length > 0 ? (
        tickers.join(", ")
      ) : (
        <span title={`CIK ${filing.cik}`}>
          <Muted>{EMPTY}</Muted>
        </span>
      )
  },
  {
    title: "Form",
    dataIndex: "form_type",
    width: 100,
    render: (form: string) => (
      <Tag className="sentinel-tag" variant="outlined">
        {form}
      </Tag>
    )
  },
  {
    title: "Item",
    key: "items",
    render: (_, filing) => <Items filing={filing} />
  },
  {
    title: "Materiality",
    key: "materiality",
    width: 104,
    render: (_, filing) => <MaterialityTag filing={filing} />
  },
  {
    title: "Pushed",
    key: "pushed",
    width: 180,
    render: (_, filing) => <Pushed slack={filing.slack} />
  },
  {
    title: "",
    key: "sec",
    width: 72,
    align: "right",
    render: (_, filing) => (
      <a
        href={filing.sec_url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Open ${filing.accession_no} on SEC EDGAR`}
        onClick={(event) => event.stopPropagation()} // the row itself opens the detail page
      >
        SEC ↗
      </a>
    )
  }
];

/** Every stored SEC filing, newest first, with its materiality and Slack delivery. */
export function AlertsPage() {
  const navigate = useNavigate();
  const [filings, setFilings] = useState<Loadable<FilingPage>>({
    data: null,
    loading: true,
    error: null
  });
  const [facets, setFacets] = useState<FilingFacets>({ tickers: [], form_types: [] });
  const [tickers, setTickers] = useState<string[]>([]);
  const [formTypes, setFormTypes] = useState<string[]>([]);
  const [range, setRange] = useState<[string, string] | null>(null);
  const [materiality, setMateriality] = useState<MaterialityFilter>("all");
  const [poller, setPoller] = useState<TaskDefinition | undefined>(undefined);
  const [now, setNow] = useState(() => new Date());
  const { zone: timeZone } = useTimeZone();
  const [page, setPage] = useState(1);

  const loadFilings = useCallback(async () => {
    setFilings((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await listFilings({
        tickers,
        formTypes,
        acceptedFrom: range?.[0],
        acceptedTo: range?.[1],
        materiality: materiality === "all" ? undefined : materiality,
        timeZone,
        limit: FILINGS_PAGE_SIZE,
        offset: (page - 1) * FILINGS_PAGE_SIZE
      });
      setFilings({ data, loading: false, error: null });
    } catch (error) {
      setFilings((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    }
  }, [tickers, formTypes, range, materiality, timeZone, page]);

  useEffect(() => {
    void loadFilings();
  }, [loadFilings]);

  // Filter options are a convenience: if they fail to load, the list still works unfiltered.
  useEffect(() => {
    getFilingFacets()
      .then(setFacets)
      .catch(() => undefined);
  }, []);

  // The poll line, re-read every minute so "last ran" and "next in" stay current. If the schedule
  // cannot be read the line is left out rather than guessed.
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      listTasks()
        .then((tasks) => {
          if (cancelled) return;
          setPoller(tasks.find((task) => task.task_id === SEC_POLL_TASK));
          setNow(new Date());
        })
        .catch(() => undefined);
    };
    refresh();
    const timer = setInterval(refresh, POLL_LINE_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const filtered =
    tickers.length > 0 || formTypes.length > 0 || range !== null || materiality !== "all";
  const poll = pollLine(poller, now);
  const zone = zoneLabel(now, timeZone);

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <PageTitle title="Alerts" count={filings.data?.total} />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Space size={8} wrap>
          <Select<string[]>
            mode="multiple"
            allowClear
            placeholder="All tickers"
            className="min-w-[200px]"
            value={tickers}
            onChange={(value) => {
              setTickers(value);
              setPage(1);
            }}
            options={facets.tickers.map((ticker) => ({ value: ticker, label: ticker }))}
          />
          <Segmented<MaterialityFilter>
            value={materiality}
            onChange={(value) => {
              setMateriality(value);
              setPage(1);
            }}
            options={[
              { value: "all", label: "All" },
              { value: "high", label: "High" },
              { value: "low", label: "Low" }
            ]}
          />
          <Select<string[]>
            mode="multiple"
            allowClear
            placeholder="All forms"
            className="min-w-[200px]"
            value={formTypes}
            onChange={(value) => {
              setFormTypes(value);
              setPage(1);
            }}
            options={facets.form_types.map((form) => ({ value: form, label: form }))}
          />
          <DatePicker.RangePicker
            allowEmpty={[true, true]}
            className="w-[370px]"
            placeholder={[`Accepted from (${zone})`, `Accepted to (${zone})`]}
            onChange={(_, [from, to]) => {
              setRange(from || to ? [from, to] : null);
              setPage(1);
            }}
          />
        </Space>
        {poll && <Muted>{poll}</Muted>}
      </div>

      {filings.error ? (
        <LoadError message={filings.error} onRetry={() => void loadFilings()} />
      ) : (
        <Table<Filing>
          rowKey="accession_no"
          onRow={(filing) => ({
            className: "cursor-pointer",
            onClick: () => navigate(`/alerts/${filing.accession_no}`)
          })}
          size="small"
          tableLayout="fixed"
          columns={columns}
          dataSource={filings.data?.items ?? []}
          loading={filings.loading}
          pagination={{
            current: page,
            pageSize: FILINGS_PAGE_SIZE,
            total: filings.data?.total ?? 0,
            showSizeChanger: false,
            onChange: setPage
          }}
          locale={{
            emptyText: (
              <Muted>{filtered ? "No filings match these filters" : "No filings yet"}</Muted>
            )
          }}
        />
      )}
    </Space>
  );
}
