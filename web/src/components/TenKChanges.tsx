import { useCallback, useEffect, useState } from "react";
import { Checkbox, Segmented, Tag, Tooltip, Typography } from "antd";
import { Link } from "react-router-dom";

import { errorMessage } from "../api/client";
import { dayLabel } from "../api/financials";
import {
  countsLabel,
  getTenKChanges,
  paragraphLabel,
  visibleGroups,
  type ParagraphChange,
  type TenKChanges as Changes,
  type TenKItem
} from "../api/tenkChanges";
import { LoadError } from "./LoadError";
import { SectionTitle } from "./PageTitle";

const ITEM_OPTIONS = [
  { label: "Risk factors", value: "1A" },
  { label: "MD&A", value: "7" }
];

const KIND: Record<ParagraphChange["kind"], { label: string; color: string; help: string }> = {
  added: { label: "Added", color: "success", help: "In this year's 10-K only." },
  removed: { label: "Removed", color: "error", help: "In last year's 10-K only." },
  changed: {
    label: "Changed",
    color: "default",
    help: "Reworded since last year: struck-out words were removed, highlighted words added."
  }
};

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

function ChangeText({ change }: { change: ParagraphChange }) {
  if (change.kind === "changed" && change.words) {
    return (
      <p className="m-0">
        {change.words.map((run, index) =>
          run.op === "delete" ? (
            <del key={index} className="bg-semantic-negative/15 text-text-secondary">
              {run.text}
            </del>
          ) : run.op === "insert" ? (
            <ins key={index} className="bg-semantic-positive/15 no-underline">
              {run.text}
            </ins>
          ) : (
            <span key={index}>{run.text}</span>
          )
        )}
      </p>
    );
  }
  return (
    <p className={change.kind === "removed" ? "m-0 text-text-secondary" : "m-0"}>{change.text}</p>
  );
}

function ChangeRow({ change, link }: { change: ParagraphChange; link: string }) {
  const kind = KIND[change.kind];
  const similarity =
    change.similarity !== null
      ? ` · ${Math.round(change.similarity * 100)}% of words the same`
      : "";
  return (
    <div className="flex flex-col gap-4 border-0 border-t border-solid border-border-subtle py-8 sm:flex-row sm:gap-12">
      <div className="shrink-0 sm:w-[76px]">
        <Tooltip title={kind.help} mouseEnterDelay={0.05}>
          <Tag className="sentinel-tag cursor-help" color={kind.color}>
            {kind.label}
          </Tag>
        </Tooltip>
      </div>
      <div className="min-w-0 max-w-[880px] flex-1 space-y-4">
        <ChangeText change={change} />
        <Typography.Text type="secondary" className="!text-[12px]">
          {`${paragraphLabel(change)}${change.numbers_only ? " · figures or dates only" : ""}${similarity} · `}
          <a href={link} target="_blank" rel="noreferrer">
            {change.kind === "removed" ? "Last year's 10-K ↗" : "This year's 10-K ↗"}
          </a>
        </Typography.Text>
      </div>
    </div>
  );
}

/** A 10-K's risk factors or MD&A against the year before: every paragraph added, removed or changed. */
export function TenKChanges({
  symbol,
  item,
  onItemChange
}: {
  symbol: string;
  item: TenKItem;
  onItemChange: (item: TenKItem) => void;
}) {
  const [state, setState] = useState<Loadable<Changes>>({ data: null, loading: true, error: null });
  const [showNumbersOnly, setShowNumbersOnly] = useState(false);

  const load = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      setState({ data: await getTenKChanges(symbol, item), loading: false, error: null });
    } catch (error) {
      setState({ data: null, loading: false, error: errorMessage(error) });
    }
  }, [symbol, item]);

  useEffect(() => {
    void load();
  }, [load]);

  const data = state.data;
  const comparison = data?.comparison ?? null;
  const groups = comparison ? visibleGroups(comparison.groups, showNumbersOnly) : [];
  const yearEnded = comparison?.new.report_date
    ? `the year ended ${dayLabel(comparison.new.report_date)}`
    : "the latest year";

  let body;
  if (state.error) {
    body = <LoadError message={state.error} onRetry={() => void load()} />;
  } else if (!data || state.loading) {
    body = <Muted>Loading…</Muted>;
  } else if (!comparison) {
    body = (
      <Muted>
        {data.filer_type === "foreign_private"
          ? `${data.symbol} files a 20-F each year, not a 10-K. Changes in its annual report are not compared yet.`
          : `No comparison yet: it needs two years of 10-Ks, and ${data.symbol} has filed fewer so far.`}
      </Muted>
    );
  } else {
    body = (
      <>
        <div className="space-y-4">
          <div>
            {`${data.item_label} in the 10-K for ${yearEnded}, filed ${dayLabel(comparison.new.filed_date)}, against the one filed ${dayLabel(comparison.old.filed_date)}. `}
            <a href={comparison.new.section_url} target="_blank" rel="noreferrer">
              {`Item ${data.item} this year ↗`}
            </a>
            {" · "}
            <a href={comparison.old.section_url} target="_blank" rel="noreferrer">
              {`last year ↗`}
            </a>
          </div>
          <Muted>{countsLabel(comparison.counts)}</Muted>
        </div>
        {comparison.counts.numbers_only > 0 && (
          <Checkbox
            checked={showNumbersOnly}
            onChange={(event) => setShowNumbersOnly(event.target.checked)}
          >
            {`Show changes in figures or dates only (${comparison.counts.numbers_only})`}
          </Checkbox>
        )}
        {groups.length === 0 ? (
          <Muted>
            {comparison.groups.length === 0
              ? "No paragraph changed in this section."
              : "Only figures or dates changed. Tick the box above to see them."}
          </Muted>
        ) : (
          groups.map((group, index) => (
            <section key={`${index}-${group.heading ?? ""}`}>
              <Typography.Text strong className="block pb-4">
                {group.heading ?? "Start of the section"}
              </Typography.Text>
              {group.changes.map((change) => (
                <ChangeRow
                  key={change.change_no}
                  change={change}
                  link={
                    change.kind === "removed"
                      ? comparison.old.section_url
                      : comparison.new.section_url
                  }
                />
              ))}
            </section>
          ))
        )}
        <Typography.Text type="secondary" className="!text-[12px]">
          Paragraphs are matched by their wording, not by a model: a changed paragraph shares at
          least 60% of its words with last year&apos;s. Figure tables are left out; the numbers are
          in Key financials above. <Link to="/glossary#10-k-changes">Glossary</Link>
        </Typography.Text>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-12">
      <div className="flex flex-wrap items-center gap-16">
        <SectionTitle>Changes in the 10-K</SectionTitle>
        <Segmented
          aria-label="Risk factors or MD&A"
          size="small"
          options={ITEM_OPTIONS}
          value={item}
          onChange={(value) => onItemChange(value as TenKItem)}
        />
      </div>
      {body}
    </div>
  );
}
