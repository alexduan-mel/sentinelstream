export type Health = {
  status: string;
  version: string;
};

export async function fetchHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  const response = await fetchImpl("/healthz", { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`Health check failed: HTTP ${response.status}`);
  }
  const body = (await response.json()) as Partial<Health>;
  if (typeof body.status !== "string" || typeof body.version !== "string") {
    throw new Error("Health check returned an unexpected payload");
  }
  return { status: body.status, version: body.version };
}
