import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType
} from "antd";
import { Link, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { formLabel } from "../api/filings";
import {
  ACTION_HELP,
  ACTION_LABEL,
  REASON_MAX,
  THESIS_MAX,
  correctEntry,
  getJournal,
  tradeLabel,
  type CorrectionKind,
  type Journal,
  type JournalCorrection,
  type JournalEntry,
  type JournalSource
} from "../api/journal";
import { LoadError } from "../components/LoadError";
import { HelpTitle } from "../components/HelpTitle";
import {
  AlphaCell,
  BetaCell,
  CallCell,
  MIN_DAYS,
  MoneyView,
  RecordOfCalls,
  ReturnCell,
  Signed
} from "../components/JournalScores";
import { PageTitle, SectionTitle } from "../components/PageTitle";
import { Timestamp } from "../components/Timestamp";
import { useTimeZone } from "../lib/timeZone";

function Muted({ children }: { children: React.ReactNode }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

export function ActionTag({ action }: { action: JournalEntry["action"] }) {
  return (
    <Tooltip title={ACTION_HELP[action]} mouseEnterDelay={0.05}>
      <Tag className="sentinel-tag cursor-help">{ACTION_LABEL[action]}</Tag>
    </Tooltip>
  );
}

/** "8-K · Nov 19, 2026", linking to Alert detail. */
function SourceLink({ source }: { source: JournalSource }) {
  const { zone } = useTimeZone();
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(source.accepted_at));
  return (
    <Link to={`/alerts/${source.accession_no}`}>{`${formLabel(source.form_type)} · ${day}`}</Link>
  );
}

const CORRECTION_LABEL: Record<CorrectionKind, string> = {
  thesis: "Thesis corrected",
  source: "Alert link changed",
  withdrawn: "Withdrawn"
};

function CorrectionLine({ correction }: { correction: JournalCorrection }) {
  let what: React.ReactNode = null;
  if (correction.kind === "thesis")
    what = <div className="whitespace-pre-wrap">{correction.thesis}</div>;
  if (correction.kind === "source")
    what = correction.source ? (
      <div>
        Now from <SourceLink source={correction.source} />
      </div>
    ) : (
      <div>Link to an alert removed</div>
    );
  return (
    <li className="mb-8">
      <div>
        <Typography.Text strong>{CORRECTION_LABEL[correction.kind]}</Typography.Text>{" "}
        <Muted>
          <Timestamp value={correction.known_at} /> · {correction.recorded_by} · Reason:{" "}
          {correction.reason}
        </Muted>
      </div>
      {what}
    </li>
  );
}

type Draft = { kind: CorrectionKind; thesis: string; reason: string };

/** The expanded row: what was first written, every correction since, and the correction actions. */
function EntryDetail({ entry, onChanged }: { entry: JournalEntry; onChanged: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = (kind: CorrectionKind) => {
    setError(null);
    setDraft({ kind, thesis: kind === "thesis" ? entry.thesis : "", reason: "" });
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.reason.trim()) return setError("Give a reason; it is kept with the correction");
    if (draft.kind === "thesis" && !draft.thesis.trim())
      return setError("The thesis can't be empty");
    setSaving(true);
    setError(null);
    try {
      await correctEntry(entry.id, {
        kind: draft.kind,
        reason: draft.reason,
        thesis: draft.kind === "thesis" ? draft.thesis : null,
        accession_no: null
      });
      setDraft(null);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  };

  const thesisChanged = entry.original_thesis !== entry.thesis;
  const sourceChanged = entry.original_source?.accession_no !== entry.source?.accession_no;

  return (
    <Space orientation="vertical" size={12} className="w-full max-w-[880px]">
      <div>
        <Muted>
          Logged <Timestamp value={entry.known_at} /> by {entry.recorded_by}
        </Muted>
      </div>
      {(thesisChanged || sourceChanged) && (
        <div>
          <Typography.Text strong>As first logged</Typography.Text>
          {thesisChanged && <div className="whitespace-pre-wrap">{entry.original_thesis}</div>}
          {sourceChanged && (
            <div>
              {entry.original_source ? (
                <>
                  From <SourceLink source={entry.original_source} />
                </>
              ) : (
                "Not from an alert"
              )}
            </div>
          )}
        </div>
      )}
      {entry.corrections.length > 0 && (
        <ul className="m-0 list-none p-0">
          {entry.corrections.map((correction, index) => (
            <CorrectionLine key={index} correction={correction} />
          ))}
        </ul>
      )}

      {!entry.withdrawn && !draft && (
        <Space size={16}>
          <Typography.Link onClick={() => start("thesis")}>Correct the thesis</Typography.Link>
          {entry.source && (
            <Typography.Link onClick={() => start("source")}>Remove the alert link</Typography.Link>
          )}
          <Typography.Link onClick={() => start("withdrawn")}>Withdraw</Typography.Link>
        </Space>
      )}

      {draft && (
        <Space orientation="vertical" size={8} className="w-full">
          <Typography.Text strong>
            {draft.kind === "thesis"
              ? "Correct the thesis"
              : draft.kind === "source"
                ? "Remove the alert link"
                : "Withdraw this entry"}
          </Typography.Text>
          <Muted>
            {draft.kind === "withdrawn"
              ? "It stays listed with your reason and stops counting. The action, ticker, trade and time can't be corrected: withdraw and log it again instead."
              : "The original stays visible above the correction."}
          </Muted>
          {draft.kind === "thesis" && (
            <Input.TextArea
              aria-label="Corrected thesis"
              rows={4}
              maxLength={THESIS_MAX}
              showCount
              value={draft.thesis}
              onChange={(event) => setDraft({ ...draft, thesis: event.target.value })}
            />
          )}
          <Input
            aria-label="Reason"
            placeholder="Reason, e.g. typo, wrong alert, logged the wrong ticker"
            maxLength={REASON_MAX}
            value={draft.reason}
            onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
          />
          {error && <Alert type="error" title={error} />}
          <Space>
            <Button type="primary" loading={saving} onClick={() => void save()}>
              {draft.kind === "withdrawn" ? "Withdraw" : "Save correction"}
            </Button>
            <Button onClick={() => setDraft(null)}>Cancel</Button>
          </Space>
        </Space>
      )}
    </Space>
  );
}

export function JournalPage() {
  const [params, setParams] = useSearchParams();
  const ticker = params.get("ticker") ?? undefined;
  const [state, setState] = useState<{
    data: Journal | null;
    loading: boolean;
    error: string | null;
  }>({ data: null, loading: true, error: null });
  const [expanded, setExpanded] = useState<number[]>([]);

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      // Filtered on the server, so the record of calls and the money view cover the same entries.
      setState({ data: await getJournal(ticker), loading: false, error: null });
    } catch (error) {
      setState({ data: null, loading: false, error: errorMessage(error) });
    }
  }, [ticker]);

  useEffect(() => {
    void load();
  }, [load]);

  const entries = state.data?.entries ?? [];
  const tickers = state.data?.tickers ?? (ticker ? [ticker] : []);

  const toggle = (id: number) =>
    setExpanded((keys) => (keys.includes(id) ? keys.filter((key) => key !== id) : [...keys, id]));

  const columns: TableColumnsType<JournalEntry> = [
    {
      title: "Logged",
      dataIndex: "known_at",
      width: 150,
      render: (value: string) => <Timestamp value={value} />
    },
    {
      title: "Ticker",
      dataIndex: "symbol",
      width: 80,
      render: (symbol: string) => (
        <Link to={`/watchlist/${encodeURIComponent(symbol)}`}>{symbol}</Link>
      )
    },
    {
      title: "Action",
      key: "action",
      width: 170,
      render: (_, entry) => (
        <Space size={4}>
          <ActionTag action={entry.action} />
          {entry.withdrawn && (
            <Tooltip title={`Withdrawn: ${entry.withdrawn.reason}`} mouseEnterDelay={0.05}>
              <Tag className="sentinel-tag cursor-help" color="error">
                Withdrawn
              </Tag>
            </Tooltip>
          )}
        </Space>
      )
    },
    {
      title: "Thesis",
      key: "thesis",
      render: (_, entry) => (
        <div className={entry.withdrawn ? "text-text-muted" : undefined}>
          <div className="line-clamp-3 whitespace-pre-wrap">{entry.thesis}</div>
          {entry.thesis !== entry.original_thesis && <Muted>Corrected</Muted>}
          {entry.source && (
            <div>
              <Muted>From </Muted>
              <SourceLink source={entry.source} />
            </div>
          )}
        </div>
      )
    },
    {
      title: "Trade",
      key: "trade",
      width: 190,
      render: (_, entry) =>
        tradeLabel(entry) ??
        (entry.action === "pass" ? (
          <Muted>Not a trade</Muted>
        ) : (
          <Tooltip
            title="No trade day or price given: measured from the first close after it was logged"
            mouseEnterDelay={0.05}
          >
            <span className="cursor-help">
              <Muted>Next close</Muted>
            </span>
          </Tooltip>
        ))
    },
    {
      title: (
        <HelpTitle
          title="Return"
          help="The stock's price change since the entry, to its Closed entry or the latest close. Hover a value for the dates and QQQ's and SPY's over the same days."
        />
      ),
      key: "return",
      width: 100,
      align: "right",
      render: (_, entry) => <ReturnCell entry={entry} />
    },
    {
      title: (
        <HelpTitle
          title="vs QQQ"
          help="The stock's return minus QQQ's over the same days: above zero, it did better than the index."
        />
      ),
      key: "vs_qqq",
      width: 100,
      align: "right",
      render: (_, entry) => <Signed value={entry.score?.vs_qqq} />
    },
    {
      title: (
        <HelpTitle
          title="Alpha"
          help={`The part of the return QQQ's moves don't explain, once the stock's beta is allowed for. Needs ${MIN_DAYS} trading days.`}
        />
      ),
      key: "alpha",
      width: 100,
      align: "right",
      render: (_, entry) => <AlphaCell entry={entry} />
    },
    {
      title: (
        <HelpTitle
          title="Beta"
          help="How much the stock moved for each 1% move in QQQ over the entry's days."
        />
      ),
      key: "beta",
      width: 80,
      align: "right",
      render: (_, entry) => <BetaCell entry={entry} />
    },
    {
      title: (
        <HelpTitle
          title="Call"
          help="Right or wrong beyond the market: Opened and Added are right when alpha is above zero; Trimmed, Closed and Pass when it is below zero."
        />
      ),
      key: "call",
      width: 80,
      render: (_, entry) => <CallCell entry={entry} />
    },
    {
      title: "",
      key: "details",
      width: 90,
      render: (_, entry) => (
        <Typography.Link onClick={() => toggle(entry.id)}>
          {expanded.includes(entry.id) ? "Hide" : "Details"}
        </Typography.Link>
      )
    }
  ];

  const open = state.data?.open_positions ?? [];

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <div className="flex items-center justify-between">
        <PageTitle title="Journal" count={state.data ? entries.length : undefined} />
        <Link to={ticker ? `/journal/new?ticker=${encodeURIComponent(ticker)}` : "/journal/new"}>
          <Button type="primary">Log a decision</Button>
        </Link>
      </div>

      <Space size={16} align="center" wrap>
        <Select
          aria-label="Ticker"
          allowClear
          placeholder="All tickers"
          className="w-[160px]"
          value={ticker}
          options={tickers.map((symbol) => ({ value: symbol, label: symbol }))}
          onChange={(value?: string) =>
            setParams(value ? { ticker: value } : {}, { replace: true })
          }
        />
        {state.data && (
          <Muted>{open.length ? `Open positions: ${open.join(", ")}` : "No open positions"}</Muted>
        )}
      </Space>

      {state.data && state.data.combined.scored > 0 && (
        <>
          <SectionTitle>Record of calls</SectionTitle>
          <RecordOfCalls combined={state.data.combined} />
        </>
      )}

      {state.error ? (
        <LoadError message={state.error} onRetry={() => void load()} />
      ) : (
        <Table<JournalEntry>
          rowKey="id"
          size="small"
          tableLayout="fixed"
          // Below this width the table scrolls sideways, so the thesis keeps room to read.
          scroll={{ x: 1400 }}
          loading={state.loading}
          columns={columns}
          dataSource={entries}
          pagination={entries.length > 50 ? { pageSize: 50, showSizeChanger: false } : false}
          locale={{
            emptyText: (
              <Muted>{ticker ? `No entries for ${ticker}` : "No decisions logged yet"}</Muted>
            )
          }}
          expandable={{
            expandedRowKeys: expanded,
            showExpandColumn: false,
            expandedRowRender: (entry) => (
              <EntryDetail entry={entry} onChanged={() => void load()} />
            )
          }}
        />
      )}

      {state.data && state.data.money.positions.length > 0 && (
        <>
          <SectionTitle>My money</SectionTitle>
          <MoneyView money={state.data.money} />
        </>
      )}

      <Muted>
        {"Your own decisions, newest first. Each is logged at the server's time and never edited: a " +
          "correction is added under the original, and a withdrawn entry stays listed with its " +
          "reason. A trade's day and price are checked against that day's prices. Returns are price " +
          "changes worked out from stored daily prices each time the page loads; dividends are not " +
          "included. See "}
        <Link to="/glossary#journal">Journal</Link> in the glossary.
      </Muted>
    </Space>
  );
}
