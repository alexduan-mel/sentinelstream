import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import dayjs from "dayjs";
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
    key: "links",
    width: 128,
    align: "right",
    render: (_, filing) => (
      <Space size={12} onClick={(event) => event.stopPropagation()}>
        <DetailsLink accessionNo={filing.accession_no} />
        <a
          href={filing.sec_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${filing.accession_no} on SEC EDGAR`}
        >
          SEC ↗
        </a>
      </Space>
    )
  }
];

/** Where the list was, so returning from a filing lands on the same filtered view and position. */
const RETURN_KEY = "alerts-return";

function rememberPosition(search: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, JSON.stringify({ search, y: window.scrollY }));
  } catch {
    // Storage can be unavailable (private windows); returning then starts at the top.
  }
}

function DetailsLink({ accessionNo }: { accessionNo: string }) {
  const { search } = useLocation();
  return (
    <Link
      to={`/alerts/${accessionNo}`}
      state={{ fromSearch: search }}
      onClick={() => rememberPosition(search)}
      aria-label={`Details of ${accessionNo}`}
    >
      Details
    </Link>
  );
}

type Filters = {
  tickers: string[];
  formTypes: string[];
  range: [string, string] | null;
  materiality: MaterialityFilter;
  page: number;
};

/** The list's filters live in the address (?ticker=NVDA&materiality=high&page=2), so Back,
 *  "← Alerts" and bookmarks all return to the same view. */
function readFilters(params: URLSearchParams): Filters {
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const level = params.get("materiality");
  return {
    tickers: params.getAll("ticker"),
    formTypes: params.getAll("form"),
    range: from || to ? [from, to] : null,
    materiality: level === "high" || level === "low" ? level : "all",
    page: Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1)
  };
}

/** Every stored SEC filing, newest first, with its materiality and Slack delivery. */
export function AlertsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const search = params.toString();
  const { tickers, formTypes, range, materiality, page } = useMemo(
    () => readFilters(new URLSearchParams(search)),
    [search]
  );
  const [filings, setFilings] = useState<Loadable<FilingPage>>({
    data: null,
    loading: true,
    error: null
  });
  const [facets, setFacets] = useState<FilingFacets>({ tickers: [], form_types: [] });
  const [poller, setPoller] = useState<TaskDefinition | undefined>(undefined);
  const [now, setNow] = useState(() => new Date());
  const { zone: timeZone } = useTimeZone();

  // Filter changes replace the address rather than adding to history, so Back from a filing returns
  // to the list as it was, not through every filter change. Any filter change goes back to page 1.
  const setFilter = useCallback(
    (key: string, value: string | string[] | null) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete(key);
          for (const item of Array.isArray(value) ? value : value ? [value] : []) {
            next.append(key, item);
          }
          if (key !== "page") next.delete("page");
          return next;
        },
        { replace: true }
      );
    },
    [setParams]
  );

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

  // Back from a filing: once its page of results is shown, scroll to where the list was.
  useEffect(() => {
    if (filings.loading || !filings.data) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(RETURN_KEY) ?? "null") as {
        search: string;
        y: number;
      } | null;
      if (saved && saved.search === location.search) window.scrollTo(0, saved.y);
      sessionStorage.removeItem(RETURN_KEY);
    } catch {
      // Unreadable or unavailable storage: stay at the top.
    }
  }, [filings.loading, filings.data, location.search]);

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
            onChange={(value) => setFilter("ticker", value)}
            options={facets.tickers.map((ticker) => ({ value: ticker, label: ticker }))}
          />
          <Segmented<MaterialityFilter>
            value={materiality}
            onChange={(value) => setFilter("materiality", value === "all" ? null : value)}
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
            onChange={(value) => setFilter("form", value)}
            options={facets.form_types.map((form) => ({ value: form, label: form }))}
          />
          <DatePicker.RangePicker
            allowEmpty={[true, true]}
            className="w-[370px]"
            placeholder={[`Accepted from (${zone})`, `Accepted to (${zone})`]}
            value={
              range ? [range[0] ? dayjs(range[0]) : null, range[1] ? dayjs(range[1]) : null] : null
            }
            onChange={(_, [from, to]) => {
              setParams(
                (current) => {
                  const next = new URLSearchParams(current);
                  for (const [key, value] of [
                    ["from", from],
                    ["to", to]
                  ] as const) {
                    if (value) next.set(key, value);
                    else next.delete(key);
                  }
                  next.delete("page");
                  return next;
                },
                { replace: true }
              );
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
            onClick: () => {
              // Selecting text in a row (to copy a reason, say) is not a request to open it.
              if (window.getSelection()?.toString()) return;
              rememberPosition(location.search);
              navigate(`/alerts/${filing.accession_no}`, {
                state: { fromSearch: location.search }
              });
            }
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
            onChange: (next) => setFilter("page", next > 1 ? String(next) : null)
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
