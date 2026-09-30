import type { AdminRun, EngineStatus } from "@algoearning/api-types";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import Link from "next/link";

import { LoadError, Table, fmtDateTime, td } from "@/components/monitor/bits";
import { EngineControl } from "@/components/monitor/engine-admin";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { ModePill, RunStatusPill } from "@/components/runs/run-bits";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Engine" };

export default async function MonitorEngine() {
  const [status, runs] = await Promise.all([
    apiGet<EngineStatus>("/v1/admin/engine"),
    apiGet<AdminRun[]>("/v1/admin/runs"),
  ]);
  if (!status.ok || !runs.ok)
    return (
      <LoadError
        what="the engine"
        message={(!status.ok ? status.error : !runs.ok ? runs.error : null)?.message ?? ""}
      />
    );
  const errors = runs.data.filter((r) => r.engine_stale).length;
  return (
    <div className="flex flex-col gap-4">
      <AutoRefresh seconds={5} />
      <div>
        <h1 className="text-2xl font-semibold">Engine</h1>
        <p className="text-sm text-muted">
          Every user&apos;s running strategies. Paper only until live orders ship.
        </p>
      </div>
      <EngineControl status={status.data} />
      {errors > 0 && (
        <StatusPill tone="warning">
          {errors} running strategies have not been stepped for 30 seconds: is the engine up?
        </StatusPill>
      )}
      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Active runs</CardTitle>
        </div>
        {runs.data.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">Nothing running.</p>
        ) : (
          <Table head={["User", "Strategy", "Mode", "Status", "Open", "P&L", "Last step"]}>
            {runs.data.map((r) => (
              <tr key={r.id}>
                <td className={`${td} max-w-52`}>
                  <Link href={`/monitor/users/${r.user_id}`} className="block truncate hover:underline">
                    {r.user_email}
                  </Link>
                </td>
                <td className={td}>
                  {r.strategy_name}{" "}
                  <span className="text-xs text-muted">
                    · {r.underlying} ×{r.multiplier}
                  </span>
                </td>
                <td className={td}>
                  <ModePill mode={r.mode} dryRun={r.dry_run} />
                </td>
                <td className={td}>
                  <RunStatusPill run={r} />
                </td>
                <td className={`${td} tabular-nums`}>{r.open_positions}</td>
                <td className={td}>
                  <Pnl value={r.realized_pnl + r.unrealized_pnl} />
                </td>
                <td className={`${td} whitespace-nowrap text-muted`}>{fmtDateTime(r.heartbeat_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
