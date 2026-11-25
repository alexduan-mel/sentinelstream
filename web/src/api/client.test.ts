import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, onUnauthorized } from "./client";

function fakeFetch(
  status: number,
  body: unknown,
  headers: Record<string, string> = {}
): typeof fetch {
  return async () =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

let unsubscribe: (() => void) | undefined;
afterEach(() => unsubscribe?.());

describe("apiFetch", () => {
  it("returns the JSON body", async () => {
    await expect(apiFetch("/api/tasks", {}, fakeFetch(200, [{ task_id: "x" }]))).resolves.toEqual([
      { task_id: "x" }
    ]);
  });

  it("returns null for an empty 204", async () => {
    await expect(
      apiFetch("/api/auth/logout", { method: "POST" }, fakeFetch(204, undefined))
    ).resolves.toBeNull();
  });

  it("raises the API's detail and signals a lost session on 401", async () => {
    const listener = vi.fn();
    unsubscribe = onUnauthorized(listener);

    const error = await apiFetch(
      "/api/tasks",
      {},
      fakeFetch(401, { detail: "Login required" })
    ).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, message: "Login required" });
    expect(listener).toHaveBeenCalledOnce();
  });

  it("does not treat a failed login as a lost session", async () => {
    const listener = vi.fn();
    unsubscribe = onUnauthorized(listener);

    await apiFetch(
      "/api/auth/login",
      { method: "POST", body: "{}" },
      fakeFetch(401, { detail: "x" })
    ).catch(() => undefined);

    expect(listener).not.toHaveBeenCalled();
  });

  it("reads the lockout from a 429", async () => {
    const error = await apiFetch(
      "/api/auth/login",
      { method: "POST", body: "{}" },
      fakeFetch(
        429,
        { detail: "Too many attempts", retry_after_seconds: 240 },
        { "Retry-After": "240" }
      )
    ).catch((e) => e);

    expect(error).toMatchObject({ status: 429, retryAfterSeconds: 240 });
  });

  it("falls back to the HTTP status when the body has no detail", async () => {
    await expect(apiFetch("/api/tasks", {}, fakeFetch(502, "Bad gateway"))).rejects.toThrow(
      "HTTP 502"
    );
  });
});
