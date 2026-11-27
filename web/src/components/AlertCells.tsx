import { Tag, Tooltip, Typography } from "antd";
import type { ReactNode } from "react";

import type { Filing, SlackDelivery } from "../api/filings";
import { EMPTY, Timestamp } from "./Timestamp";

/** Cells shared by the Alerts list and the alert detail page. */

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

/** Explains a cell on hover, straight away (a native title waits about a second). */
export function Hint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip title={text} mouseEnterDelay={0.05}>
      <span className="cursor-help">{children}</span>
    </Tooltip>
  );
}

/** DESIGN.md, Alerts: `High` amber, `Low` neutral. The rule's reason shows on hover. */
export function MaterialityTag({ filing }: { filing: Filing }) {
  if (!filing.materiality) {
    return (
      <Hint text="Not classified yet">
        <Muted>{EMPTY}</Muted>
      </Hint>
    );
  }
  const high = filing.materiality === "high";
  const tag = (
    <Tag className="sentinel-tag" color={high ? "warning" : "default"}>
      {high ? "High" : "Low"}
    </Tag>
  );
  return filing.materiality_reason ? <Hint text={filing.materiality_reason}>{tag}</Hint> : tag;
}

/** The Slack time once delivered; "Queued" while waiting; "Skipped" if it will never be sent. */
export function Pushed({ slack }: { slack: SlackDelivery | null }) {
  if (slack?.status === "delivered") return <Timestamp value={slack.delivered_at} />;
  if (slack?.status === "pending") {
    return (
      <Hint text={slack.detail ? `Last attempt: ${slack.detail}` : "Sent on the next poll"}>
        <Muted>Queued</Muted>
      </Hint>
    );
  }
  if (slack?.status === "skipped") {
    return (
      <Hint text={`Not pushed: ${slack.detail ?? "no reason recorded"}`}>
        <Muted>Skipped</Muted>
      </Hint>
    );
  }
  return <Muted>{EMPTY}</Muted>;
}
