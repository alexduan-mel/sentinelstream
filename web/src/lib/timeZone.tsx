import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { browserZone, US_EASTERN } from "./time";

/** "et": US Eastern, the market's clock. "local": the browser's own zone. */
export type ZoneChoice = "et" | "local";

const STORAGE_KEY = "sentinelstream.timeZone";

type TimeZoneValue = {
  choice: ZoneChoice;
  /** The IANA zone times are shown in now. */
  zone: string;
  /** The zone the hover text uses: the one not shown. */
  otherZone: string;
  /** The browser's zone; equal to US Eastern for viewers there, when the toggle is pointless. */
  localZone: string;
  setChoice: (choice: ZoneChoice) => void;
};

function resolve(choice: ZoneChoice, localZone: string) {
  const zone = choice === "local" ? localZone : US_EASTERN;
  return { zone, otherZone: zone === US_EASTERN ? localZone : US_EASTERN };
}

const fallbackLocal = browserZone();
const TimeZoneContext = createContext<TimeZoneValue>({
  choice: "et",
  localZone: fallbackLocal,
  ...resolve("et", fallbackLocal),
  setChoice: () => undefined
});

// The choice is a per-browser convenience: if storage is unavailable, the page starts in ET.
function readChoice(): ZoneChoice {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "local" ? "local" : "et";
  } catch {
    return "et";
  }
}

function saveChoice(choice: ZoneChoice): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // Not remembered; the toggle still works for this visit.
  }
}

export function TimeZoneProvider({
  children,
  localZone = browserZone()
}: {
  children: ReactNode;
  localZone?: string;
}) {
  const [choice, setChoiceState] = useState<ZoneChoice>(readChoice);
  const setChoice = useCallback((next: ZoneChoice) => {
    setChoiceState(next);
    saveChoice(next);
  }, []);
  const value = useMemo(
    () => ({ choice, localZone, ...resolve(choice, localZone), setChoice }),
    [choice, localZone, setChoice]
  );
  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>;
}

export function useTimeZone(): TimeZoneValue {
  return useContext(TimeZoneContext);
}
