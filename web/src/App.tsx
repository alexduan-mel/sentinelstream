import { useEffect, useState } from "react";
import { Layout, Tag, Typography } from "antd";

import { fetchHealth, type Health } from "./api/health";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <Layout className="min-h-screen">
      <Layout.Header className="flex items-center justify-between">
        <Typography.Title level={4} className="!mb-0">
          SentinelStream
        </Typography.Title>
        {health && <Tag color="green">build {health.version.slice(0, 7)}</Tag>}
        {error && <Tag color="red">{error}</Tag>}
      </Layout.Header>
      <Layout.Content className="p-24">
        <Typography.Paragraph type="secondary">
          Watchlist filing alerts and the task-runs view are on the way.
        </Typography.Paragraph>
      </Layout.Content>
    </Layout>
  );
}
