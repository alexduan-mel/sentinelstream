import { Spin } from "antd";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { errorMessage } from "../api/client";
import { getSettings } from "../api/settings";
import { setDisplayZone } from "../lib/time";
import { LoadError } from "./LoadError";

type State = { status: "loading" } | { status: "ready" } | { status: "error"; message: string };

/**
 * Reads /api/settings before the signed-in pages render, so every time is shown in the display zone
 * from the first paint. If the settings cannot be read the pages are not shown with a guessed zone.
 */
export function SettingsGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(() => {
    setState({ status: "loading" });
    getSettings()
      .then((settings) => {
        setDisplayZone(settings.display_timezone);
        setState({ status: "ready" });
      })
      .catch((error: unknown) => setState({ status: "error", message: errorMessage(error) }));
  }, []);

  useEffect(load, [load]);

  if (state.status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spin />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="p-24">
        <LoadError message={state.message} onRetry={load} />
      </div>
    );
  }
  return <>{children}</>;
}
