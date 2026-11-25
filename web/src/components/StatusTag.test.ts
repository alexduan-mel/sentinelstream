import { describe, expect, it } from "vitest";

import { RUN_STATUSES } from "../api/tasks";
import { STATUS_TAG_COLOR } from "./StatusTag";

describe("STATUS_TAG_COLOR", () => {
  it("follows DESIGN.md: green, amber, red, accent, grey", () => {
    expect(STATUS_TAG_COLOR).toEqual({
      succeeded: "success",
      partial: "warning",
      failed: "error",
      running: "processing",
      pending: "default",
      skipped: "default"
    });
    expect(Object.keys(STATUS_TAG_COLOR).sort()).toEqual([...RUN_STATUSES].sort());
  });
});
