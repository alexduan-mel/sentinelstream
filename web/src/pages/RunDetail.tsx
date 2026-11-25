import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Descriptions, Spin, Typography } from "antd";

import { errorMessage } from "../api/client";
import { getRun, rerun, type TaskRunDetail } from "../api/tasks";
import { LoadError } from "../components/LoadError";

/** The expanded row of a task run: trace ID, full error, meta as JSON, and "Run again". */
export function RunDetail({ runId, onQueued }: { runId: number; onQueued: () => Promise<void> }) {
  const [run, setRun] = useState<TaskRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRun(await getRun(runId));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onRunAgain = async () => {
    setQueueing(true);
    setQueueError(null);
    try {
      await rerun(runId);
      await onQueued();
    } catch (caught) {
      setQueueError(errorMessage(caught));
    } finally {
      setQueueing(false);
    }
  };

  if (error) return <LoadError message={error} onRetry={() => void load()} />;
  if (run === null) return <Spin size="small" />;

  const items = [
    {
      key: "trace",
      label: "Trace ID",
      children: (
        <Typography.Text className="font-mono" copyable>
          {run.trace_id}
        </Typography.Text>
      )
    },
    ...(run.error_message
      ? [
          {
            key: "error",
            label: "Error",
            children: (
              <Typography.Text type="danger" className="whitespace-pre-wrap">
                {run.error_message}
              </Typography.Text>
            )
          }
        ]
      : []),
    {
      key: "meta",
      label: "Meta",
      children: (
        <pre className="m-0 whitespace-pre-wrap font-mono text-secondary">
          {JSON.stringify(run.meta, null, 2)}
        </pre>
      )
    }
  ];

  return (
    <div className="flex flex-col gap-8">
      <Descriptions size="small" column={1} items={items} />
      {queueError && (
        <Alert type="error" title={queueError} closable={{ onClose: () => setQueueError(null) }} />
      )}
      <div>
        <Button size="small" loading={queueing} onClick={() => void onRunAgain()}>
          Run again
        </Button>
      </div>
    </div>
  );
}
