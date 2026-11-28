import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useMatch, useNavigate, useSearchParams } from "react-router-dom";
import dayjs from "dayjs";
import {
  Alert,
  Button,
  DatePicker,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType
} from "antd";
import { CloseOutlined, LeftOutlined, ReloadOutlined, RightOutlined } from "@ant-design/icons";

import { errorMessage } from "../api/client";
import {
  formLabel,
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
import { AlertDetail, clearDetailCache } from "./AlertDetail";
import { formatRelative, formatTime, zoneLabel } from "../lib/time";
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

function DetailsLink({ accessionNo }: { accessionNo: string }) {
  const { search } = useLocation();
  return (
    <Link
      to={`/alerts/${accessionNo}${search}`}
      state={{ fromList: true }}
      aria-label={`Details of ${accessionNo}`}
    >
      Details
    </Link>
  );
}

/** Lists already loaded this session, by query: going back to one shows it at once, as it was. It
 *  is reloaded only by Refresh (or "Show" on the new-filings notice), never on its own. */
type CachedPage = { data: FilingPage; at: number };
const listCache = new Map<string, CachedPage>();

export function clearListCache(): void {
  listCache.clear();
}

/** Below this width the reading pane takes the whole screen instead of sitting beside the list. */
const NARROW_PX = 768;

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW_PX);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < NARROW_PX);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return narrow;
}

/** Arrow keys and Esc belong to whatever is being typed in, not to the list. */
function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.closest("[role=combobox], [role=listbox], .ant-picker-dropdown") !== null
  );
}

/** One row of the narrow list beside the reading pane: ticker and form, then when, High marked. */
function CompactRow({
  filing,
  selected,
  onOpen
}: {
  filing: Filing;
  selected: boolean;
  onOpen: () => void;
}) {
  const { zone } = useTimeZone();
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);
  const who = filing.tickers.join(", ") || `CIK ${filing.cik}`;
  return (
    <button
      ref={ref}
      type="button"
      onClick={onOpen}
      aria-current={selected ? "true" : undefined}
      aria-label={`${who} ${filing.form_type}, accepted ${formatTime(filing.accepted_at, undefined, zone).text}`}
      className={`block w-full cursor-pointer border-0 border-b border-l-2 border-solid border-b-border-default px-8 py-6 text-left ${
        selected
          ? "border-l-semantic-info bg-bg-elevated"
          : "border-l-transparent bg-transparent hover:bg-bg-elevated"
      }`}
    >
      <div className="flex items-center justify-between gap-4">
        <span className="truncate font-medium text-text-primary">{`${who} · ${formLabel(filing.form_type)}`}</span>
        {filing.materiality === "high" && (
          <span className="text-semantic-warning text-secondary" aria-label="High">
            High
          </span>
        )}
      </div>
      <div className="text-secondary text-text-muted">
        {formatTime(filing.accepted_at, undefined, zone).text}
      </div>
    </button>
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

/**
 * Every stored SEC filing, newest first, with its materiality and Slack delivery. Opening one slides
 * its detail in beside a narrow list (``/alerts/:accession``); the list stays loaded underneath, so
 * closing the detail is instant and keeps your place (DESIGN.md, Alerts).
 */
export function AlertsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const openMatch = useMatch("/alerts/:accessionNo");
  const openAccession = openMatch?.params.accessionNo ?? null;
  const narrow = useNarrow();
  const search = params.toString();
  const { tickers, formTypes, range, materiality, page } = useMemo(
    () => readFilters(new URLSearchParams(search)),
    [search]
  );
  const [filings, setFilings] = useState<Loadable<FilingPage> & { at: number | null }>({
    data: null,
    loading: true,
    error: null,
    at: null
  });
  const [newCount, setNewCount] = useState(0);
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

  const query = useMemo(
    () => ({
      tickers,
      formTypes,
      acceptedFrom: range?.[0],
      acceptedTo: range?.[1],
      materiality: materiality === "all" ? undefined : materiality,
      timeZone,
      limit: FILINGS_PAGE_SIZE,
      offset: (page - 1) * FILINGS_PAGE_SIZE
    }),
    [tickers, formTypes, range, materiality, timeZone, page]
  );
  const cacheKey = useMemo(() => JSON.stringify(query), [query]);

  const loadFilings = useCallback(
    async (force = false) => {
      const cached = force ? undefined : listCache.get(cacheKey);
      if (cached) {
        setFilings({ data: cached.data, loading: false, error: null, at: cached.at });
        return;
      }
      setFilings((current) => ({ ...current, loading: true, error: null }));
      try {
        const data = await listFilings(query);
        const at = Date.now();
        listCache.set(cacheKey, { data, at });
        setFilings({ data, loading: false, error: null, at });
        setNewCount(0);
      } catch (error) {
        setFilings((current) => ({ ...current, loading: false, error: errorMessage(error) }));
      }
    },
    [cacheKey, query]
  );

  useEffect(() => {
    void loadFilings();
  }, [loadFilings]);

  const refresh = useCallback(() => {
    // A refresh also forgets loaded details: their Slack delivery may have moved on.
    listCache.clear();
    clearDetailCache();
    void loadFilings(true);
  }, [loadFilings]);

  // Filter options are a convenience: if they fail to load, the list still works unfiltered.
  useEffect(() => {
    getFilingFacets()
      .then(setFacets)
      .catch(() => undefined);
  }, []);

  // Every minute: re-read the poller's schedule for the poll line, and check quietly whether newer
  // filings match the filters. The list itself never changes until Refresh or "Show".
  const loadedTotal = useRef<number | null>(null);
  loadedTotal.current = filings.data?.total ?? null;
  const queryRef = useRef(query);
  queryRef.current = query;
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      listTasks()
        .then((tasks) => {
          if (cancelled) return;
          setPoller(tasks.find((task) => task.task_id === SEC_POLL_TASK));
          setNow(new Date());
        })
        .catch(() => undefined);
    };
    const check = () => {
      if (document.visibilityState === "hidden" || loadedTotal.current === null) return;
      listFilings({ ...queryRef.current, limit: 1, offset: 0 })
        .then((latest) => {
          if (cancelled || loadedTotal.current === null) return;
          setNewCount(Math.max(0, latest.total - loadedTotal.current));
        })
        .catch(() => undefined);
    };
    tick();
    const timer = setInterval(() => {
      tick();
      check();
    }, POLL_LINE_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const items = useMemo(() => filings.data?.items ?? [], [filings.data]);
  const total = filings.data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / FILINGS_PAGE_SIZE));
  const index = openAccession ? items.findIndex((f) => f.accession_no === openAccession) : -1;

  // Open, step and close. Opening adds one history entry; stepping replaces it, so Back closes the
  // pane rather than walking through every alert looked at.
  const listScroll = useRef(0);
  const open = useCallback(
    (accession: string) => {
      if (!openAccession) listScroll.current = window.scrollY;
      navigate(`/alerts/${accession}${location.search}`, {
        replace: openAccession !== null,
        state: openAccession ? location.state : { fromList: true }
      });
    },
    [navigate, location.search, location.state, openAccession]
  );
  const close = useCallback(() => {
    if ((location.state as { fromList?: boolean } | null)?.fromList) navigate(-1);
    else navigate(`/${location.search}`, { replace: true });
  }, [navigate, location.search, location.state]);

  // Back on the full list, return to where it was scrolled.
  const wasOpen = useRef(openAccession !== null);
  useLayoutEffect(() => {
    if (wasOpen.current && !openAccession) window.scrollTo(0, listScroll.current);
    if (!wasOpen.current && openAccession) window.scrollTo(0, 0);
    wasOpen.current = openAccession !== null;
  }, [openAccession]);

  // Stepping past either end of a page loads the next or previous page, then opens its first or
  // last filing.
  const pending = useRef<"first" | "last" | null>(null);
  useEffect(() => {
    if (!pending.current || filings.loading || items.length === 0 || !openAccession) return;
    const target = pending.current === "first" ? items[0] : items[items.length - 1];
    pending.current = null;
    open(target.accession_no);
  }, [items, filings.loading, openAccession, open]);

  const canNewer = index > 0 || (index === 0 && page > 1);
  const canOlder = index >= 0 ? index < items.length - 1 || page < lastPage : items.length > 0;
  const step = useCallback(
    (direction: 1 | -1) => {
      if (index < 0) {
        if (direction === 1 && items.length > 0) open(items[0].accession_no);
        return;
      }
      const target = index + direction;
      if (target >= 0 && target < items.length) open(items[target].accession_no);
      else if (direction === 1 && page < lastPage) {
        pending.current = "first";
        setFilter("page", String(page + 1));
      } else if (direction === -1 && page > 1) {
        pending.current = "last";
        setFilter("page", page - 1 > 1 ? String(page - 1) : null);
      }
    },
    [index, items, open, page, lastPage, setFilter]
  );

  useEffect(() => {
    if (!openAccession) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || typingIn(event.target)) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        step(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Escape") {
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openAccession, step, close]);

  const filtered =
    tickers.length > 0 || formTypes.length > 0 || range !== null || materiality !== "all";
  const poll = pollLine(poller, now);
  const zone = zoneLabel(now, timeZone);
  const updated =
    filings.at === null ? null : `Updated ${formatRelative(new Date(filings.at), now)}`;

  const freshness = (
    <Space size={12} wrap>
      {poll && <Muted>{poll}</Muted>}
      {updated && <Muted>{updated.replace("<1m ago", "just now")}</Muted>}
      <Button
        size="small"
        icon={<ReloadOutlined />}
        loading={filings.loading && filings.data !== null}
        onClick={refresh}
      >
        Refresh
      </Button>
    </Space>
  );

  const notice =
    newCount > 0 ? (
      <Alert
        type="info"
        title={`${newCount} new ${newCount === 1 ? "filing" : "filings"} since this list was loaded`}
        action={
          <Button
            size="small"
            onClick={() => {
              if (page > 1) setFilter("page", null);
              refresh();
            }}
          >
            Show
          </Button>
        }
      />
    ) : null;

  const toolbar = (
    <Space size={4} align="center">
      <Typography.Text type="secondary" className="!text-secondary hidden lg:inline">
        ↑ ↓ to move · Esc to close
      </Typography.Text>
      <Tooltip title="Newer (↑)" mouseEnterDelay={0.3}>
        <Button
          size="small"
          icon={<LeftOutlined />}
          aria-label="Newer filing"
          disabled={!canNewer}
          onClick={() => step(-1)}
        >
          Newer
        </Button>
      </Tooltip>
      <Tooltip title="Older (↓)" mouseEnterDelay={0.3}>
        <Button size="small" aria-label="Older filing" disabled={!canOlder} onClick={() => step(1)}>
          Older <RightOutlined />
        </Button>
      </Tooltip>
      <Tooltip title="Close (Esc)" mouseEnterDelay={0.3}>
        <Button
          size="small"
          type="text"
          icon={<CloseOutlined />}
          aria-label="Close"
          onClick={close}
        />
      </Tooltip>
    </Space>
  );

  const filterSummary = [
    tickers.join(", "),
    materiality !== "all" ? (materiality === "high" ? "High" : "Low") : "",
    formTypes.join(", "),
    range ? `${range[0] || "…"} to ${range[1] || "…"}` : ""
  ]
    .filter(Boolean)
    .join(" · ");

  const fullList = (
    <Space orientation="vertical" size={16} className={`w-full ${openAccession ? "!hidden" : ""}`}>
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
      </div>

      {notice}

      {filings.error ? (
        <LoadError message={filings.error} onRetry={() => void loadFilings(true)} />
      ) : (
        <Table<Filing>
          rowKey="accession_no"
          onRow={(filing) => ({
            className: "cursor-pointer",
            onClick: () => {
              // Selecting text in a row (to copy a reason, say) is not a request to open it.
              if (window.getSelection()?.toString()) return;
              open(filing.accession_no);
            }
          })}
          size="small"
          tableLayout="fixed"
          columns={columns}
          dataSource={items}
          loading={filings.loading && filings.data === null}
          pagination={{
            current: page,
            pageSize: FILINGS_PAGE_SIZE,
            total,
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

  const detail = openAccession ? (
    <AlertDetail key={openAccession} accessionNo={openAccession} toolbar={toolbar} />
  ) : null;

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <div className="flex flex-wrap items-center justify-between gap-8">
        <PageTitle title="Alerts" count={filings.data?.total} />
        {freshness}
      </div>

      {fullList}

      {openAccession && narrow && (
        <section aria-label="Alert detail" className="sentinel-slide-in">
          <div className="mb-12">
            <Typography.Link onClick={close}>← Alerts</Typography.Link>
          </div>
          {detail}
        </section>
      )}

      {openAccession && !narrow && (
        // Fills the window under the top bar (48px) and the page's padding, so the list and the
        // detail scroll on their own.
        <div className="flex flex-col gap-8" style={{ height: "calc(100vh - 140px)" }}>
          {(filterSummary || notice) && (
            <div className="flex flex-wrap items-center gap-12">
              {filterSummary && <Muted>{`Filtered: ${filterSummary}`}</Muted>}
              {notice}
            </div>
          )}
          <div className="flex min-h-0 flex-1 border-0 border-t border-solid border-border-default">
            <nav
              aria-label="Alerts list"
              className="w-[220px] shrink-0 overflow-y-auto border-0 border-r border-solid border-border-default"
            >
              {items.map((filing) => (
                <CompactRow
                  key={filing.accession_no}
                  filing={filing}
                  selected={filing.accession_no === openAccession}
                  onOpen={() => open(filing.accession_no)}
                />
              ))}
              {total > 0 && (
                <div className="px-8 py-8 text-secondary text-text-muted">
                  {`${(page - 1) * FILINGS_PAGE_SIZE + 1}–${(page - 1) * FILINGS_PAGE_SIZE + items.length} of ${total.toLocaleString("en-US")}`}
                </div>
              )}
            </nav>
            <section
              aria-label="Alert detail"
              className="sentinel-slide-in min-w-0 flex-1 overflow-y-auto pl-24 pt-16"
            >
              {detail}
            </section>
          </div>
        </div>
      )}
    </Space>
  );
}
