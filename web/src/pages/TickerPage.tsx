import { useCallback, useEffect, useState } from "react";
import { Segmented, Space, Table, Tooltip, Typography, type TableColumnsType } from "antd";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import {
  METHOD_LABEL,
  PERIODS_SHOWN,
  exactValue,
  formatValue,
  getKeyFinancials,
  missingReason,
  periodLabel,
  primarySource,
  sourceLine,
  dayLabel,
  type Basis,
  type FinancialPeriod,
  type KeyFinancials,
  type MetricInfo,
  type MetricValue
} from "../api/financials";
import { FILER_TYPE_LABEL } from "../api/instruments";
import { Hint } from "../components/AlertCells";
import { HelpTitle } from "../components/HelpTitle";
import { LoadError } from "../components/LoadError";
import { PageTitle, SectionTitle } from "../components/PageTitle";
import { EMPTY } from "../components/Timestamp";

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

const BASIS_OPTIONS = [
  { label: "Quarters", value: "quarterly" },
  { label: "Fiscal years", value: "annual" }
];

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

/** The hover text of a number: the exact figure, how it was arrived at, and every filed value used. */
function ValueHelp({ value, metric }: { value: MetricValue; metric: MetricInfo }) {
  // A ratio is worked out from dollar amounts; every other metric's sources share its unit.
  const sourceUnit = metric.unit === "ratio" ? "USD" : metric.unit;
  return (
    <div className="space-y-4">
      <div className="font-medium">{exactValue(value.value, metric.unit)}</div>
      <div>{METHOD_LABEL[value.method]}</div>
      {value.sources.map((source) => (
        <div key={`${source.accession_no}-${source.concept}-${source.period_end}`}>
          {sourceLine(source)}
          {value.method !== "reported" && ` · ${exactValue(source.value, sourceUnit)}`}
        </div>
      ))}
      <div>Click to open the filing on SEC.</div>
    </div>
  );
}

function ValueCell({
  value,
  metric,
  period,
  basis
}: {
  value: MetricValue | null;
  metric: MetricInfo;
  period: FinancialPeriod;
  basis: Basis;
}) {
  if (!value) {
    return (
      <Hint text={missingReason(metric, period, basis)}>
        <Muted>{EMPTY}</Muted>
      </Hint>
    );
  }
  const source = primarySource(value);
  const text = formatValue(value.value, metric.unit);
  return (
    <Tooltip title={<ValueHelp value={value} metric={metric} />} mouseEnterDelay={0.05}>
      {source ? (
        <a href={source.url} target="_blank" rel="noreferrer">
          {text}
        </a>
      ) : (
        <span>{text}</span>
      )}
    </Tooltip>
  );
}

type Row = { metric: MetricInfo };

function columns(data: KeyFinancials): TableColumnsType<Row> {
  return [
    {
      title: "",
      key: "metric",
      fixed: "left",
      width: 200,
      render: (_, row) => <HelpTitle title={row.metric.label} help={row.metric.definition} />
    },
    ...data.periods.map((period) => ({
      title: (
        <div className="text-right">
          <div>{periodLabel(period, data.basis)}</div>
          {period.fiscal_year !== null && (
            <Typography.Text type="secondary" className="!text-[12px] !font-normal">
              {`Ended ${dayLabel(period.end)}`}
            </Typography.Text>
          )}
        </div>
      ),
      key: `${period.start}-${period.end}`,
      align: "right" as const,
      width: 140,
      render: (_: unknown, row: Row) => (
        <ValueCell
          value={period.values[row.metric.key] ?? null}
          metric={row.metric}
          period={period}
          basis={data.basis}
        />
      )
    })),
    // Takes up the spare width, so a few fiscal years stay next to their labels.
    { title: "", key: "rest" }
  ];
}

/** One watchlist company: its key financials from XBRL, each number linked to its filing. */
export function TickerPage() {
  const { symbol = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const requested: Basis = search.get("basis") === "annual" ? "annual" : "quarterly";
  const [state, setState] = useState<Loadable<KeyFinancials>>({
    data: null,
    loading: true,
    error: null
  });

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      let result = await getKeyFinancials(symbol, requested);
      // A foreign private issuer files only annual reports, so its quarters are always empty.
      if (result.filer_type === "foreign_private" && result.basis === "quarterly") {
        result = await getKeyFinancials(symbol, "annual");
      }
      setState({ data: result, loading: false, error: null });
    } catch (error) {
      setState({ data: null, loading: false, error: errorMessage(error) });
    }
  }, [symbol, requested]);

  useEffect(() => {
    void load();
  }, [load]);

  const setBasis = (value: Basis) => {
    const next = new URLSearchParams(search);
    if (value === "annual") next.set("basis", "annual");
    else next.delete("basis");
    setSearch(next, { replace: true });
  };

  const data = state.data;
  const foreign = data?.filer_type === "foreign_private";
  const shown: Basis = data?.basis ?? requested;

  const back = <Link to="/watchlist">← Watchlist</Link>;
  const upper = symbol.toUpperCase();
  const identity = data
    ? [
        data.name,
        data.filer_type ? `${FILER_TYPE_LABEL[data.filer_type]} filer` : null,
        `CIK ${data.cik}`
      ].filter((part): part is string => Boolean(part))
    : [];

  return (
    <Space orientation="vertical" size={16} className="w-full">
      {back}
      <div>
        <PageTitle title={data?.symbol ?? upper} />
        {identity.length > 0 && <Muted>{identity.join(" · ")}</Muted>}
      </div>

      {state.error ? (
        <LoadError message={state.error} onRetry={() => void load()} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-16">
            <SectionTitle>Key financials</SectionTitle>
            {foreign ? (
              <Muted>
                Files a 20-F once a year and its quarterly results without XBRL, so only fiscal
                years are shown.
              </Muted>
            ) : (
              <Segmented
                aria-label="Quarters or fiscal years"
                size="small"
                options={BASIS_OPTIONS}
                value={requested}
                onChange={(value) => setBasis(value as Basis)}
              />
            )}
          </div>

          {data && !state.loading && data.periods.length === 0 ? (
            <Muted>
              {shown === "annual"
                ? "No fiscal years filed in XBRL yet."
                : "No quarters filed in XBRL yet."}
            </Muted>
          ) : (
            <Table<Row>
              size="small"
              rowKey={(row) => row.metric.key}
              loading={state.loading}
              pagination={false}
              scroll={{ x: "max-content" }}
              columns={data ? columns(data) : []}
              dataSource={data ? data.metrics.map((metric) => ({ metric })) : []}
            />
          )}

          <Typography.Text type="secondary" className="!text-[12px]">
            {`The latest ${PERIODS_SHOWN} ${shown === "annual" ? "fiscal years" : "quarters"} at most, newest first, `}
            as filed with SEC in XBRL. When a later filing restates a period, its figure replaces
            the earlier one. Click a number to open the filing it came from; hover it for the exact
            figure and how it was worked out.{" "}
            <Link to="/glossary#company-financials">Glossary</Link>
          </Typography.Text>
        </>
      )}
    </Space>
  );
}
