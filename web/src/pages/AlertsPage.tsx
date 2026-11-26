import { useCallback, useEffect, useState } from "react";
import { DatePicker, Select, Space, Table, Tag, Typography, type TableColumnsType } from "antd";

import { errorMessage } from "../api/client";
import {
  getFilingFacets,
  itemLabel,
  listFilings,
  type Filing,
  type FilingFacets,
  type FilingItem,
  type FilingPage
} from "../api/filings";
import { LoadError } from "../components/LoadError";
import { PageTitle } from "../components/PageTitle";
import { EMPTY, Timestamp } from "../components/Timestamp";

export const FILINGS_PAGE_SIZE = 50;

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

function Items({ items }: { items: FilingItem[] }) {
  if (items.length === 0) return null;
  return (
    <>
      {items.map((item) => {
        const label = itemLabel(item);
        return (
          <div key={item.number} className="truncate" title={label}>
            {label}
          </div>
        );
      })}
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
    dataIndex: "items",
    render: (items: FilingItem[]) => <Items items={items} />
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
      >
        SEC ↗
      </a>
    )
  }
];

/** Every stored SEC filing, newest first. Materiality and Slack delivery are added in M1. */
export function AlertsPage() {
  const [filings, setFilings] = useState<Loadable<FilingPage>>({
    data: null,
    loading: true,
    error: null
  });
  const [facets, setFacets] = useState<FilingFacets>({ tickers: [], form_types: [] });
  const [tickers, setTickers] = useState<string[]>([]);
  const [formTypes, setFormTypes] = useState<string[]>([]);
  const [range, setRange] = useState<[string, string] | null>(null);
  const [page, setPage] = useState(1);

  const loadFilings = useCallback(async () => {
    setFilings((current) => ({ ...current, loading: true, error: null }));
    try {
      const data = await listFilings({
        tickers,
        formTypes,
        acceptedFrom: range?.[0],
        acceptedTo: range?.[1],
        limit: FILINGS_PAGE_SIZE,
        offset: (page - 1) * FILINGS_PAGE_SIZE
      });
      setFilings({ data, loading: false, error: null });
    } catch (error) {
      setFilings((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    }
  }, [tickers, formTypes, range, page]);

  useEffect(() => {
    void loadFilings();
  }, [loadFilings]);

  // Filter options are a convenience: if they fail to load, the list still works unfiltered.
  useEffect(() => {
    getFilingFacets()
      .then(setFacets)
      .catch(() => undefined);
  }, []);

  const filtered = tickers.length > 0 || formTypes.length > 0 || range !== null;

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <PageTitle title="Alerts" count={filings.data?.total} />

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
          className="w-[320px]"
          placeholder={["Accepted from (ET)", "Accepted to (ET)"]}
          onChange={(_, [from, to]) => {
            setRange(from || to ? [from, to] : null);
            setPage(1);
          }}
        />
      </Space>

      {filings.error ? (
        <LoadError message={filings.error} onRetry={() => void loadFilings()} />
      ) : (
        <Table<Filing>
          rowKey="accession_no"
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
