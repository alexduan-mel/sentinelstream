import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  DatePicker,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Tooltip,
  Typography
} from "antd";
import type { Dayjs } from "dayjs";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { errorMessage } from "../api/client";
import { formLabel, getFiling, itemLabel, type FilingDetail } from "../api/filings";
import { listInstruments, type Instrument } from "../api/instruments";
import {
  ACTIONS,
  ACTION_HELP,
  ACTION_LABEL,
  THESIS_MAX,
  actionAllowed,
  getJournal,
  isTrade,
  logEntry,
  type JournalAction
} from "../api/journal";
import { LoadError } from "../components/LoadError";
import { PageTitle } from "../components/PageTitle";
import { Timestamp } from "../components/Timestamp";

function Muted({ children }: { children: React.ReactNode }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-4">
        <Typography.Text strong>{label}</Typography.Text>
      </div>
      {children}
    </div>
  );
}

/** Today's date in New York, the market's calendar. */
function marketToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

/** A trade day can't be in the future or on a weekend (holidays are refused by the API). */
export function tradeDayDisabled(day: Dayjs, today: string = marketToday()): boolean {
  return day.format("YYYY-MM-DD") > today || day.day() === 0 || day.day() === 6;
}

type Loaded = { instruments: Instrument[]; open: string[]; filing: FilingDetail | null };

export function LogDecisionPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const alertParam = params.get("alert");

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | undefined>(params.get("ticker") ?? undefined);
  const [action, setAction] = useState<JournalAction | undefined>();
  const [thesis, setThesis] = useState("");
  const [linkAlert, setLinkAlert] = useState(Boolean(alertParam));
  const [tradeDay, setTradeDay] = useState<Dayjs | null>(null);
  const [price, setPrice] = useState<number | null>(null);
  const [shares, setShares] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoadError(null);
    try {
      const [instruments, journal, filing] = await Promise.all([
        listInstruments(),
        getJournal(),
        alertParam ? getFiling(alertParam) : Promise.resolve(null)
      ]);
      setLoaded({ instruments, open: journal.open_positions, filing });
    } catch (caught) {
      setLoadError(errorMessage(caught));
    }
  };

  useEffect(() => {
    void load();
    // Loaded once per page; the address only changes by leaving it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choices = useMemo(
    () =>
      (loaded?.instruments ?? []).filter(
        (i) =>
          i.role === "watchlist" && i.asset_type === "equity" && i.onboarding_status === "ready"
      ),
    [loaded]
  );
  const open = symbol ? (loaded?.open ?? []).includes(symbol) : false;

  // An action that no longer fits the chosen ticker is cleared rather than sent and refused.
  useEffect(() => {
    if (action && symbol && loaded && !actionAllowed(action, open)) setAction(undefined);
  }, [action, symbol, open, loaded]);

  const back = alertParam ? (
    <Link to={`/alerts/${alertParam}`}>← Alert</Link>
  ) : (
    <Link to="/journal">← Journal</Link>
  );

  if (loadError) {
    return (
      <Space orientation="vertical" size={16} className="w-full">
        {back}
        <LoadError message={loadError} onRetry={() => void load()} />
      </Space>
    );
  }
  if (!loaded) {
    return (
      <Space orientation="vertical" size={16} className="w-full">
        {back}
        <Muted>Loading…</Muted>
      </Space>
    );
  }

  const filing = loaded.filing;
  const trade = action ? isTrade(action) : false;
  // An alert is linked only to a ticker it was filed for (the API refuses others).
  const alertFits = Boolean(filing && symbol && filing.tickers.includes(symbol));

  const submit = async () => {
    if (!symbol) return setError("Pick a ticker");
    if (!action) return setError("Pick what you did");
    if (!thesis.trim()) return setError("Write the thesis: why, in your own words");
    if (trade && (tradeDay === null) !== (price === null))
      return setError("A trade needs both the day and the price per share, or neither");
    setSaving(true);
    setError(null);
    try {
      await logEntry({
        symbol,
        action,
        thesis,
        accession_no: linkAlert && alertFits && alertParam ? alertParam : null,
        traded_on: trade && tradeDay ? tradeDay.format("YYYY-MM-DD") : null,
        price: trade ? price : null,
        shares: trade ? shares : null
      });
      navigate(`/journal?ticker=${encodeURIComponent(symbol)}`);
    } catch (caught) {
      setError(errorMessage(caught));
      setSaving(false);
    }
  };

  return (
    <Space orientation="vertical" size={16} className="w-full">
      {back}
      <PageTitle title="Log a decision" />

      <Space orientation="vertical" size={16} className="w-full max-w-[640px]">
        <Field label="Ticker">
          <Select
            aria-label="Ticker"
            placeholder="Pick a watchlist company"
            className="w-[200px]"
            value={symbol}
            onChange={setSymbol}
            options={choices.map((i) => ({
              value: i.symbol,
              label: `${i.symbol} · ${i.name ?? ""}`
            }))}
          />
          {symbol && (
            <div className="mt-4">
              <Muted>
                {open
                  ? `${symbol} has an open position in the Journal`
                  : `${symbol} has no open position in the Journal`}
              </Muted>
            </div>
          )}
        </Field>

        {filing && (
          <Field label="From alert">
            {symbol && !alertFits ? (
              <Muted>{`This alert is not for ${symbol}, so the entry won't be linked to it`}</Muted>
            ) : linkAlert ? (
              <Space size={8} wrap>
                <Link to={`/alerts/${filing.accession_no}`}>
                  {[formLabel(filing.form_type), ...filing.items.map(itemLabel)].join(" · ")}
                </Link>
                <Muted>
                  accepted <Timestamp value={filing.accepted_at} />
                </Muted>
                <Typography.Link onClick={() => setLinkAlert(false)}>
                  Don&apos;t link
                </Typography.Link>
              </Space>
            ) : (
              <Space size={8}>
                <Muted>Not linked to an alert</Muted>
                <Typography.Link onClick={() => setLinkAlert(true)}>Link it again</Typography.Link>
              </Space>
            )}
          </Field>
        )}

        <Field label="What you did">
          <Radio.Group
            aria-label="What you did"
            optionType="button"
            value={action}
            onChange={(event) => setAction(event.target.value as JournalAction)}
          >
            {ACTIONS.map((value) => {
              const allowed = !symbol || actionAllowed(value, open);
              const button = (
                <Radio.Button key={value} value={value} disabled={!allowed}>
                  {ACTION_LABEL[value]}
                </Radio.Button>
              );
              return allowed ? (
                button
              ) : (
                <Tooltip
                  key={value}
                  mouseEnterDelay={0.05}
                  title={
                    value === "opened"
                      ? `${symbol} already has an open position`
                      : `${symbol} has no open position; log Opened first`
                  }
                >
                  {button}
                </Tooltip>
              );
            })}
          </Radio.Group>
          {action && (
            <div className="mt-4">
              <Muted>{ACTION_HELP[action]}</Muted>
            </div>
          )}
        </Field>

        <Field label="Thesis">
          <Input.TextArea
            aria-label="Thesis"
            rows={5}
            maxLength={THESIS_MAX}
            showCount
            placeholder="Why, in your own words, as you see it now"
            value={thesis}
            onChange={(event) => setThesis(event.target.value)}
          />
        </Field>

        {trade && (
          <Field label="Trade details (optional)">
            <Space size={8} wrap>
              <DatePicker
                aria-label="Trade day"
                placeholder="Trade day"
                value={tradeDay}
                onChange={setTradeDay}
                disabledDate={(day) => tradeDayDisabled(day)}
              />
              <InputNumber<number>
                aria-label="Price per share"
                placeholder="Price per share"
                prefix="$"
                min={0.000001}
                className="w-[170px]"
                value={price}
                onChange={setPrice}
              />
              <InputNumber<number>
                aria-label="Shares"
                placeholder="Shares"
                min={0.000001}
                className="w-[140px]"
                value={shares}
                onChange={setShares}
              />
            </Space>
            <div className="mt-4">
              <Muted>
                The day and price you traded at, as you traded them (before any later split). The
                price is checked against that day&apos;s low–high range; one for today is checked
                after today&apos;s close. Shares are optional.
              </Muted>
            </div>
          </Field>
        )}

        {error && <Alert type="error" title={error} />}

        <Space orientation="vertical" size={8}>
          <Space>
            <Button type="primary" loading={saving} onClick={() => void submit()}>
              Log decision
            </Button>
            <Link to={alertParam ? `/alerts/${alertParam}` : "/journal"}>
              <Button>Cancel</Button>
            </Link>
          </Space>
          <Muted>
            It is logged at the time you press the button and can&apos;t be edited afterwards, only
            corrected or withdrawn with the original kept. Without a trade day and price, it is
            measured from the first close after it is logged.
          </Muted>
        </Space>
      </Space>
    </Space>
  );
}
