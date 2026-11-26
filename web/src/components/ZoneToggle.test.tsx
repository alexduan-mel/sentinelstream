// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { TimeZoneProvider } from "../lib/timeZone";
import { installDomStubs } from "../test/dom";
import { Timestamp } from "./Timestamp";
import { ZoneToggle } from "./TopBar";

// A plain in-memory store: Node 25's own localStorage global shadows jsdom's and is unusable here.
const stored = new Map<string, string>();
beforeAll(() => {
  installDomStubs();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => void stored.set(key, value),
      removeItem: (key: string) => void stored.delete(key),
      clear: () => stored.clear()
    }
  });
});
afterEach(() => {
  cleanup();
  stored.clear();
});

const ACCEPTED = "2026-11-26T21:05:00Z"; // 16:05 ET, 08:05 AEDT on Nov 27
const NOW = new Date("2026-12-10T00:00:00Z");

function page(localZone: string) {
  return render(
    <TimeZoneProvider localZone={localZone}>
      <ZoneToggle />
      <Timestamp value={ACCEPTED} now={NOW} />
    </TimeZoneProvider>
  );
}

describe("ZoneToggle", () => {
  it("starts in ET, switches to the browser's zone and remembers it", async () => {
    page("Australia/Melbourne");
    expect(screen.getByText("Nov 26 16:05 ET")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("radio")[1]);
    expect(screen.getByText("Nov 27 08:05 AEDT")).toBeTruthy();
    expect(window.localStorage.getItem("sentinelstream.timeZone")).toBe("local");

    fireEvent.mouseEnter(screen.getByText("Nov 27 08:05 AEDT"));
    expect(await screen.findByText("Nov 26 16:05 ET")).toBeTruthy();

    cleanup();
    page("Australia/Melbourne");
    expect(screen.getByText("Nov 27 08:05 AEDT")).toBeTruthy();
  });

  it("is hidden for viewers whose own zone is US Eastern", () => {
    page("America/New_York");
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByText("Nov 26 16:05 ET")).toBeTruthy();
  });
});
