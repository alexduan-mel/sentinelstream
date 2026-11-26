import { Tooltip, Typography } from "antd";

import { formatInZone, formatTime } from "../lib/time";
import { useTimeZone } from "../lib/timeZone";

export const EMPTY = "—";

/** A time in the zone picked in the top bar. Hovering shows the same moment in the other zone. */
export function Timestamp({ value, now }: { value: string | null; now?: Date }) {
  const { zone, otherZone } = useTimeZone();
  if (!value) return <Typography.Text type="secondary">{EMPTY}</Typography.Text>;
  const { text, relative } = formatTime(value, now, zone);
  const shown = (
    <span>
      {text}
      {relative && <Typography.Text type="secondary"> {relative}</Typography.Text>}
    </span>
  );
  if (otherZone === zone) return shown;
  return (
    <Tooltip title={formatInZone(value, otherZone)} mouseEnterDelay={0.05}>
      <span className="cursor-help">{shown}</span>
    </Tooltip>
  );
}
