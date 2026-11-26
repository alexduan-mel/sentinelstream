import { apiFetch } from "./client";

export type Settings = { display_timezone: string };

export function getSettings(): Promise<Settings> {
  return apiFetch<Settings>("/api/settings");
}
