import { describe, expect, it } from "vitest";

import { fetchHealth } from "./health";

function fakeFetch(status: number, body: unknown): typeof fetch {
  return async () => new Response(JSON.stringify(body), { status });
}

describe("fetchHealth", () => {
  it("returns the status and build version", async () => {
    const health = await fetchHealth(fakeFetch(200, { status: "ok", version: "abc1234" }));
    expect(health).toEqual({ status: "ok", version: "abc1234" });
  });

  it("throws on an HTTP error", async () => {
    await expect(fetchHealth(fakeFetch(503, {}))).rejects.toThrow("HTTP 503");
  });

  it("throws on a malformed payload", async () => {
    await expect(fetchHealth(fakeFetch(200, { status: "ok" }))).rejects.toThrow(
      "unexpected payload"
    );
  });
});
