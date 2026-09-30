import { StatusPill } from "@algoearning/ui";

import { fetchHealth } from "@/lib/api";

/** Server component: shows whether the web server can reach the API (the phase-2 integration check). */
export async function ApiStatus() {
  const h = await fetchHealth();
  return h ? (
    <StatusPill tone="success">
      API {h.version} · {h.env}
    </StatusPill>
  ) : (
    <StatusPill tone="danger">API unreachable</StatusPill>
  );
}
