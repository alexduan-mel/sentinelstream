import { InfoCircleOutlined } from "@ant-design/icons";
import { Tooltip } from "antd";

/** A column title with an ⓘ that explains it on hover. */
export function HelpTitle({ title, help }: { title: string; help: string }) {
  return (
    <span className="whitespace-nowrap">
      {title}{" "}
      <Tooltip title={help} mouseEnterDelay={0.05}>
        <InfoCircleOutlined aria-label={`About ${title}`} className="cursor-help text-text-muted" />
      </Tooltip>
    </span>
  );
}
