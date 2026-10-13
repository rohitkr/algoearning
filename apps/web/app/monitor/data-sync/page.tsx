import type { DataSyncCoverage, MarketDataAdmin, SyncJobOut } from "@algoearning/api-types";

import { DataSyncPanel } from "@/components/monitor/data-sync-admin";
import { LoadError } from "@/components/monitor/bits";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Data sync" };

export default async function MonitorDataSync({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const q = new URLSearchParams();
  for (const k of ["underlying", "start", "end"] as const) if (sp[k]) q.set(k, sp[k]);
  const [cov, jobs, feed] = await Promise.all([
    apiGet<DataSyncCoverage>(`/v1/admin/data-sync/coverage?${q}`),
    apiGet<SyncJobOut[]>("/v1/admin/data-sync/jobs"),
    apiGet<MarketDataAdmin>("/v1/admin/market-data"),
  ]);
  if (!cov.ok) return <LoadError what="the stored history" message={cov.error.message} />;
  if (!jobs.ok) return <LoadError what="the downloads" message={jobs.error.message} />;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Data sync</h1>
        <p className="text-sm text-muted">
          The one-minute history that backtests run on: what is stored, what is missing, and a button to
          download the rest from your price provider.
        </p>
      </div>
      <DataSyncPanel
        coverage={cov.data}
        initialJobs={jobs.data}
        logins={feed.ok ? feed.data.logins : []}
        callsToday={feed.ok ? feed.data.api_calls_today : null}
      />
    </div>
  );
}
