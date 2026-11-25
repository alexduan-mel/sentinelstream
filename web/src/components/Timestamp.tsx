import { Typography } from "antd";

import { formatTime } from "../lib/time";

export const EMPTY = "—";

export function Timestamp({ value, now }: { value: string | null; now?: Date }) {
  if (!value) return <Typography.Text type="secondary">{EMPTY}</Typography.Text>;
  const { text, relative } = formatTime(value, now);
  return (
    <span title={value}>
      {text}
      {relative && <Typography.Text type="secondary"> {relative}</Typography.Text>}
    </span>
  );
}
