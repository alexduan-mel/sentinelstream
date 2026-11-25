/** JSON calls to the SentinelStream API. The session cookie travels automatically (same origin). */

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Called whenever an API call answers 401, so the app can return to the sign-in page. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return `HTTP ${status}`;
}

function retryAfterOf(response: Response, body: unknown): number | null {
  if (body && typeof body === "object" && "retry_after_seconds" in body) {
    const value = Number((body as { retry_after_seconds: unknown }).retry_after_seconds);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const header = Number(response.headers.get("Retry-After"));
  return Number.isFinite(header) && header > 0 ? header : null;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetchImpl(path, { ...init, headers, credentials: "same-origin" });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") {
      unauthorizedListeners.forEach((listener) => listener());
    }
    throw new ApiError(
      response.status,
      detailOf(body, response.status),
      retryAfterOf(response, body)
    );
  }
  return body as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
