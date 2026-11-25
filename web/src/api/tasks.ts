import { apiFetch } from "./client";

export const RUN_STATUSES = [
  "pending",
  "running",
  "succeeded",
  "partial",
  "skipped",
  "failed"
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export type TaskDefinition = {
  task_id: string;
  label: string;
  schedule_label: string;
  trigger_mode: "manual" | "automatic";
  enabled: boolean;
  next_run_at: string | null;
  last_run: { id: number; status: RunStatus; created_at: string } | null;
};

export type TaskRunSummary = {
  id: number;
  task_id: string;
  run_mode: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  fetched_count: number;
  inserted_count: number;
  deduped_count: number;
  error_message: string | null;
};

export type TaskRunDetail = TaskRunSummary & {
  trace_id: string;
  requested_by: string | null;
  tickers: string[];
  as_of_date: string | null;
  force_refresh: boolean;
  meta: Record<string, unknown>;
};

export type TaskRunPage = { items: TaskRunSummary[]; total: number };
export type QueuedRun = { run_id: number; status: "pending" };

export type RunFilters = {
  taskId?: string;
  status?: RunStatus;
  limit: number;
  offset: number;
};

export function listTasks(): Promise<TaskDefinition[]> {
  return apiFetch<TaskDefinition[]>("/api/tasks");
}

export function runTask(taskId: string): Promise<QueuedRun> {
  return apiFetch<QueuedRun>(`/api/tasks/${encodeURIComponent(taskId)}/run`, { method: "POST" });
}

export function listRuns(filters: RunFilters): Promise<TaskRunPage> {
  const params = new URLSearchParams({
    limit: String(filters.limit),
    offset: String(filters.offset)
  });
  if (filters.taskId) params.set("task_id", filters.taskId);
  if (filters.status) params.set("status", filters.status);
  return apiFetch<TaskRunPage>(`/api/task-runs?${params.toString()}`);
}

export function getRun(runId: number): Promise<TaskRunDetail> {
  return apiFetch<TaskRunDetail>(`/api/task-runs/${runId}`);
}

export function rerun(runId: number): Promise<QueuedRun> {
  return apiFetch<QueuedRun>(`/api/task-runs/${runId}/rerun`, { method: "POST" });
}

export function isActive(status: RunStatus | undefined): boolean {
  return status === "pending" || status === "running";
}
