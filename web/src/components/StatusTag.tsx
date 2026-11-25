import { Tag } from "antd";

import type { RunStatus } from "../api/tasks";

/** DESIGN.md, Tasks: succeeded muted green, partial amber, failed red, running accent, pending and skipped grey. */
export const STATUS_TAG_COLOR: Record<RunStatus, string> = {
  succeeded: "success",
  partial: "warning",
  failed: "error",
  running: "processing",
  pending: "default",
  skipped: "default"
};

export function StatusTag({ status }: { status: RunStatus }) {
  return (
    <Tag className="sentinel-tag" color={STATUS_TAG_COLOR[status]}>
      {status}
    </Tag>
  );
}
