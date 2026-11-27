import { Button, Dropdown, Layout, Segmented, Tabs, Typography } from "antd";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { US_EASTERN, zoneLabel } from "../lib/time";
import { useTimeZone, type ZoneChoice } from "../lib/timeZone";

/** Only pages that exist get a tab (DESIGN.md: hide what is unfinished). */
export const NAV_ITEMS = [
  { key: "/", label: "Alerts" },
  { key: "/watchlist", label: "Watchlist" },
  { key: "/tasks", label: "Tasks" }
];

/** The nav tab for a path: Alerts for "/" and alert detail pages, other tabs also for their sub-paths. */
export function activeNavKey(pathname: string): string {
  const match = NAV_ITEMS.find((item) =>
    item.key === "/"
      ? pathname === "/" || pathname.startsWith("/alerts/")
      : pathname === item.key || pathname.startsWith(`${item.key}/`)
  );
  return match?.key ?? "";
}

/** ET | AEDT: which clock the page's times use. Hidden for viewers whose own zone is US Eastern. */
export function ZoneToggle() {
  const { choice, localZone, setChoice } = useTimeZone();
  if (localZone === US_EASTERN) return null;
  return (
    <Segmented<ZoneChoice>
      size="small"
      aria-label="Time zone"
      value={choice}
      onChange={setChoice}
      options={[
        { value: "et", label: "ET", title: "US Eastern, the market's clock" },
        { value: "local", label: zoneLabel(new Date(), localZone), title: localZone }
      ]}
    />
  );
}

export function TopBar({ username }: { username: string }) {
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const active = activeNavKey(location.pathname);

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
      <ZoneToggle />
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
