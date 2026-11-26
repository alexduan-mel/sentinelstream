// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { getDisplayZone, setDisplayZone, US_EASTERN } from "../lib/time";
import { installDomStubs } from "../test/dom";
import { SettingsGate } from "./SettingsGate";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setDisplayZone(US_EASTERN);
});

describe("SettingsGate", () => {
  it("sets the display zone before showing the pages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ display_timezone: "Australia/Melbourne" })))
    );
    render(<SettingsGate>pages</SettingsGate>);

    expect(await screen.findByText("pages")).toBeTruthy();
    expect(getDisplayZone()).toBe("Australia/Melbourne");
  });

  it("shows the error with Retry instead of guessing a zone", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        if (calls === 1) {
          return new Response(
            JSON.stringify({ detail: "DISPLAY_TIMEZONE 'x' is not an IANA time zone" }),
            {
              status: 503
            }
          );
        }
        return new Response(JSON.stringify({ display_timezone: "Europe/London" }));
      })
    );
    render(<SettingsGate>pages</SettingsGate>);

    expect(await screen.findByText(/DISPLAY_TIMEZONE/)).toBeTruthy();
    expect(screen.queryByText("pages")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("pages")).toBeTruthy();
    expect(getDisplayZone()).toBe("Europe/London");
  });
});
