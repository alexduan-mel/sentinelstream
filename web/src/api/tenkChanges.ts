import { apiFetch } from "./client";

/** What changed in a company's latest 10-K since the year before, one section at a time (ADR 0023). */

export type TenKItem = "1A" | "7";

export type TenK = {
  accession_no: string;
  filed_date: string;
  /** The fiscal year's last day. */
  report_date: string | null;
  heading: string | null;
  /** The section in the 10-K as filed, opened at its heading. */
  section_url: string;
  index_url: string;
};

export type WordRun = { op: "equal" | "delete" | "insert"; text: string };

export type ParagraphChange = {
  change_no: number;
  kind: "added" | "removed" | "changed";
  old_paragraph_no: number | null;
  new_paragraph_no: number | null;
  similarity: number | null;
  /** Only figures, dates or months differ. */
  numbers_only: boolean;
  /** This year's text; last year's for a removed paragraph. */
  text: string;
  /** A changed paragraph, word by word against last year's. */
  words: WordRun[] | null;
};

export type ChangeGroup = { heading: string | null; changes: ParagraphChange[] };

/** Claude's points on what changed, each citing the changes it rests on (ADR 0024). */
export type ChangeSummary = {
  /** ok: points passed every check; rejected / refused: nothing to show; no_changes: only figures moved. */
  status: "ok" | "rejected" | "refused" | "no_changes";
  model: string | null;
  written_at: string;
  points: { text: string; change_nos: number[] }[];
  rejected_count: number;
};

export type Counts = {
  unchanged: number;
  moved: number;
  changed: number;
  numbers_only: number;
  added: number;
  removed: number;
};

export type TenKChanges = {
  symbol: string;
  cik: string;
  filer_type: "domestic" | "foreign_private" | null;
  item: TenKItem;
  item_label: string;
  comparison: {
    new: TenK;
    old: TenK;
    counts: Counts;
    computed_at: string;
    groups: ChangeGroup[];
    summary: ChangeSummary | null;
  } | null;
};

export function getTenKChanges(symbol: string, item: TenKItem): Promise<TenKChanges> {
  const query = new URLSearchParams({ item });
  return apiFetch<TenKChanges>(
    `/api/instruments/${encodeURIComponent(symbol)}/tenk-changes?${query.toString()}`
  );
}

/** The groups with numbers-only changes dropped unless shown; groups left empty are dropped too. */
export function visibleGroups(groups: ChangeGroup[], showNumbersOnly: boolean): ChangeGroup[] {
  if (showNumbersOnly) return groups;
  return groups
    .map((group) => ({ ...group, changes: group.changes.filter((c) => !c.numbers_only) }))
    .filter((group) => group.changes.length > 0);
}

/** "47 changed (17 only in figures or dates) · 20 added · 8 removed · 43 unchanged". */
export function countsLabel(counts: Counts): string {
  const changed =
    counts.numbers_only > 0
      ? `${counts.changed} changed (${counts.numbers_only} only in figures or dates)`
      : `${counts.changed} changed`;
  const unchanged = counts.unchanged + counts.moved;
  return [
    changed,
    `${counts.added} added`,
    `${counts.removed} removed`,
    `${unchanged} unchanged`
  ].join(" · ");
}

/** "Paragraph 37, was 35" and the like: where the change sits in each year's section. */
export function paragraphLabel(change: ParagraphChange): string {
  switch (change.kind) {
    case "added":
      return `Paragraph ${change.new_paragraph_no} this year`;
    case "removed":
      return `Paragraph ${change.old_paragraph_no} last year`;
    case "changed":
      return change.old_paragraph_no === change.new_paragraph_no
        ? `Paragraph ${change.new_paragraph_no}`
        : `Paragraph ${change.new_paragraph_no}, was ${change.old_paragraph_no}`;
  }
}
