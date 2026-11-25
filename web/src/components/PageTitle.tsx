import { Typography } from "antd";

/** Page title with an optional muted count, e.g. "Tasks · 2". */
export function PageTitle({ title, count }: { title: string; count?: number }) {
  return (
    <Typography.Title level={1} className="!m-0 !text-page-title">
      {title}
      {count !== undefined && (
        <Typography.Text type="secondary" className="!text-page-title !font-normal">
          {" "}
          · {count}
        </Typography.Text>
      )}
    </Typography.Title>
  );
}

export function SectionTitle({ children }: { children: string }) {
  return (
    <Typography.Title level={2} className="!m-0 !text-section-title">
      {children}
    </Typography.Title>
  );
}
