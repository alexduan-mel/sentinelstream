import { Tooltip, Typography } from "antd";

import { formatTime, usEasternTime } from "../lib/time";

export const EMPTY = "—";

/** A time in the display zone. When that is not US Eastern, hovering shows the market's clock too. */
export function Timestamp({ value, now }: { value: string | null; now?: Date }) {
  if (!value) return <Typography.Text type="secondary">{EMPTY}</Typography.Text>;
  const { text, relative } = formatTime(value, now);
  const eastern = usEasternTime(value);
  const shown = (
    <span>
      {text}
      {relative && <Typography.Text type="secondary"> {relative}</Typography.Text>}
    </span>
  );
  if (!eastern) return shown;
  return (
    <Tooltip title={eastern} mouseEnterDelay={0.05}>
      <span className="cursor-help">{shown}</span>
    </Tooltip>
  );
}
