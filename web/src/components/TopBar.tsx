import { Button, Dropdown, Layout, Tabs, Typography } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

/** Only pages that exist get a tab (DESIGN.md: hide what is unfinished). */
export const NAV_ITEMS = [{ key: "/tasks", label: "Tasks" }];

export function TopBar({ username }: { username: string }) {
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const active = NAV_ITEMS.find((item) => location.pathname.startsWith(item.key))?.key ?? "";

  return (
    <Layout.Header className="flex items-center gap-32 border-0 border-b border-solid border-border-default">
      <Typography.Text strong className="!text-section-title">
        SentinelStream
      </Typography.Text>
      <Tabs
        className="sentinel-nav-tabs flex-1"
        activeKey={active}
        items={NAV_ITEMS}
        onChange={(key) => navigate(key)}
      />
      <Dropdown
        trigger={["click"]}
        menu={{
          items: [{ key: "sign-out", label: "Sign out" }],
          onClick: ({ key }) => {
            if (key === "sign-out") void signOut();
          }
        }}
      >
        <Button type="text">{username}</Button>
      </Dropdown>
    </Layout.Header>
  );
}
