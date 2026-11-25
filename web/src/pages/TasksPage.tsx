import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Select, Space, Table, Typography, type TableColumnsType } from "antd";

import { errorMessage } from "../api/client";
import {
  RUN_STATUSES,
  isActive,
  listRuns,
  listTasks,
  runTask,
  type RunStatus,
  type TaskDefinition,
  type TaskRunPage,
  type TaskRunSummary
} from "../api/tasks";
import { LoadError } from "../components/LoadError";
import { PageTitle, SectionTitle } from "../components/PageTitle";
import { StatusTag } from "../components/StatusTag";
import { EMPTY, Timestamp } from "../components/Timestamp";
import { formatDuration } from "../lib/time";
import { RunDetail } from "./RunDetail";

export const RUNS_PAGE_SIZE = 50;
export const POLL_INTERVAL_MS = 10_000;

type Loadable<T> = { data: T | null; loading: boolean; error: string | null };

function Muted({ children }: { children: string }) {
  return <Typography.Text type="secondary">{children}</Typography.Text>;
}

export function TasksPage() {
  const [tasks, setTasks] = useState<Loadable<TaskDefinition[]>>({
    data: null,
    loading: true,
    error: null
  });
  const [runs, setRuns] = useState<Loadable<TaskRunPage>>({
    data: null,
    loading: true,
    error: null
  });
  const [taskFilter, setTaskFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<RunStatus | undefined>();
  const [page, setPage] = useState(1);
  const [queueing, setQueueing] = useState<string | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);

  const loadTasks = useCallback(async (quiet = false) => {
    if (!quiet) setTasks((current) => ({ ...current, loading: true, error: null }));
    try {
      setTasks({ data: await listTasks(), loading: false, error: null });
    } catch (error) {
      setTasks((current) => ({ ...current, loading: false, error: errorMessage(error) }));
    }
  }, []);

  const loadRuns = useCallback(
    async (quiet = false) => {
      if (!quiet) setRuns((current) => ({ ...current, loading: true, error: null }));
      try {
        const data = await listRuns({
          taskId: taskFilter,
          status: statusFilter,
          limit: RUNS_PAGE_SIZE,
          offset: (page - 1) * RUNS_PAGE_SIZE
        });
        setRuns({ data, loading: false, error: null });
      } catch (error) {
        setRuns((current) => ({ ...current, loading: false, error: errorMessage(error) }));
      }
    },
    [taskFilter, statusFilter, page]
  );

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  // Poll every 10 s while any run on screen, or any task's last run, is pending or running.
  const polling =
    (runs.data?.items.some((run) => isActive(run.status)) ?? false) ||
    (tasks.data?.some((task) => isActive(task.last_run?.status)) ?? false);
  useEffect(() => {
    if (!polling) return undefined;
    const timer = window.setInterval(() => {
      void loadTasks(true);
      void loadRuns(true);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [polling, loadTasks, loadRuns]);

  const refreshAfterQueue = useCallback(async () => {
    await Promise.all([loadTasks(true), loadRuns(true)]);
  }, [loadTasks, loadRuns]);

  const onRunNow = async (taskId: string) => {
    setQueueing(taskId);
    setQueueError(null);
    try {
      await runTask(taskId);
      await refreshAfterQueue();
    } catch (error) {
      setQueueError(errorMessage(error));
    } finally {
      setQueueing(null);
    }
  };

  const taskColumns: TableColumnsType<TaskDefinition> = [
    {
      title: "Task",
      dataIndex: "task_id",
      render: (taskId: string) => <span className="whitespace-nowrap font-mono">{taskId}</span>
    },
    { title: "Schedule", dataIndex: "schedule_label" },
    {
      title: "Trigger",
      dataIndex: "trigger_mode",
      render: (mode: TaskDefinition["trigger_mode"]) =>
        mode === "automatic" ? "Automatic" : "Manual"
    },
    {
      title: "Next run",
      dataIndex: "next_run_at",
      render: (value: string | null) => <Timestamp value={value} />
    },
    {
      title: "Last status",
      key: "last_status",
      render: (_, task) =>
        task.last_run ? <StatusTag status={task.last_run.status} /> : <Muted>{EMPTY}</Muted>
    },
    {
      title: "",
      key: "run_now",
      align: "right",
      render: (_, task) => (
        <Button
          size="small"
          disabled={!task.enabled || (queueing !== null && queueing !== task.task_id)}
          loading={queueing === task.task_id}
          onClick={() => void onRunNow(task.task_id)}
        >
          Run now
        </Button>
      )
    }
  ];

  const count = (value: number) => value.toLocaleString("en-US");
  const runColumns: TableColumnsType<TaskRunSummary> = [
    {
      title: "Started",
      dataIndex: "started_at",
      width: 180,
      render: (value: string | null) => <Timestamp value={value} />
    },
    {
      title: "Task",
      dataIndex: "task_id",
      width: 200,
      render: (taskId: string) => <span className="whitespace-nowrap font-mono">{taskId}</span>
    },
    { title: "Mode", dataIndex: "run_mode", width: 100 },
    {
      title: "Status",
      dataIndex: "status",
      width: 110,
      render: (status: RunStatus) => <StatusTag status={status} />
    },
    {
      title: "Duration",
      dataIndex: "duration_seconds",
      align: "right",
      width: 100,
      render: (value: number | null) =>
        value === null ? <Muted>{EMPTY}</Muted> : formatDuration(value)
    },
    { title: "Fetched", dataIndex: "fetched_count", align: "right", width: 90, render: count },
    { title: "Inserted", dataIndex: "inserted_count", align: "right", width: 90, render: count },
    { title: "Deduped", dataIndex: "deduped_count", align: "right", width: 90, render: count },
    {
      title: "Error",
      dataIndex: "error_message",
      ellipsis: true,
      render: (value: string | null) =>
        value ? <Typography.Text type="danger">{value}</Typography.Text> : null
    }
  ];

  return (
    <Space orientation="vertical" size={24} className="w-full">
      <PageTitle title="Tasks" count={tasks.data?.length} />

      <Space orientation="vertical" size={8} className="w-full">
        <SectionTitle>Task definitions</SectionTitle>
        {tasks.error ? (
          <LoadError message={tasks.error} onRetry={() => void loadTasks()} />
        ) : (
          <>
            {queueError && (
              <Alert
                type="error"
                title={queueError}
                closable={{ onClose: () => setQueueError(null) }}
              />
            )}
            <Table<TaskDefinition>
              rowKey="task_id"
              size="small"
              columns={taskColumns}
              dataSource={tasks.data ?? []}
              loading={tasks.loading}
              pagination={false}
              locale={{ emptyText: <Muted>No task definitions</Muted> }}
            />
          </>
        )}
      </Space>

      <Space orientation="vertical" size={8} className="w-full">
        <SectionTitle>Recent runs</SectionTitle>
        <Space size={8}>
          <Select<string>
            allowClear
            placeholder="All tasks"
            className="w-[220px]"
            value={taskFilter}
            onChange={(value) => {
              setTaskFilter(value);
              setPage(1);
            }}
            options={(tasks.data ?? []).map((task) => ({
              value: task.task_id,
              label: task.task_id
            }))}
          />
          <Select<RunStatus>
            allowClear
            placeholder="All statuses"
            className="w-[160px]"
            value={statusFilter}
            onChange={(value) => {
              setStatusFilter(value);
              setPage(1);
            }}
            options={RUN_STATUSES.map((status) => ({ value: status, label: status }))}
          />
        </Space>
        {runs.error ? (
          <LoadError message={runs.error} onRetry={() => void loadRuns()} />
        ) : (
          <Table<TaskRunSummary>
            rowKey="id"
            size="small"
            columns={runColumns}
            dataSource={runs.data?.items ?? []}
            loading={runs.loading}
            expandable={{
              expandedRowRender: (run) => <RunDetail runId={run.id} onQueued={refreshAfterQueue} />
            }}
            pagination={{
              current: page,
              pageSize: RUNS_PAGE_SIZE,
              total: runs.data?.total ?? 0,
              showSizeChanger: false,
              onChange: setPage
            }}
            locale={{ emptyText: <Muted>No runs</Muted> }}
          />
        )}
      </Space>
    </Space>
  );
}
