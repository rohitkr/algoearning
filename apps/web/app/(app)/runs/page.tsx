import type { RiskSettings, Run } from "@algoearning/api-types";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import Link from "next/link";

import { AutoRefresh } from "@/components/runs/auto-refresh";
import { RiskSettingsCard, StopAllButton, StopRunButton } from "@/components/runs/run-actions";
import { ModePill, RunStatusPill, fmt } from "@/components/runs/run-bits";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Running" };

export default async function RunsPage() {
  await requireUser();
  const [active, history, risk] = await Promise.all([
    apiGet<Run[]>("/v1/runs"),
    apiGet<Run[]>("/v1/runs?active=false&limit=10"),
    apiGet<RiskSettings>("/v1/me/risk"),
  ]);
  if (!active.ok)
    return <StatusPill tone="danger">Could not load your runs: {active.error.message}</StatusPill>;
  const total = active.data.reduce((a, r) => a + r.realized_pnl + r.unrealized_pnl, 0);
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <AutoRefresh />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Running</h1>
          <p className="text-sm text-muted">
            Deployed strategies. They trade every day on live prices until you stop them.
          </p>
        </div>
        <div className="flex items-center gap-4">
          {active.data.length > 0 && (
            <span className="text-sm">
              P&amp;L <Pnl value={total} className="text-lg font-semibold" />
            </span>
          )}
          <StopAllButton count={active.data.length} />
        </div>
      </div>

      {active.data.length === 0 ? (
        <Card className="text-center text-sm text-muted">
          Nothing running.{" "}
          <Link href="/strategies" className="text-primary-text underline">
            Deploy a ready strategy
          </Link>{" "}
          from your strategies.
        </Card>
      ) : (
        <ul className="flex flex-col gap-2">
          {active.data.map((r) => (
            <li key={r.id} className="rounded-xl border border-border bg-surface p-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-48 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/runs/${r.id}`} className="font-semibold hover:underline">
                      {r.strategy_name}
                    </Link>
                    <ModePill mode={r.mode} />
                    <RunStatusPill run={r} />
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    {r.underlying} · ×{r.multiplier} · {r.open_positions} open · since{" "}
                    {fmt(r.started_at ?? r.created_at)}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-muted">P&amp;L</p>
                  <Pnl value={r.realized_pnl + r.unrealized_pnl} className="font-semibold" />
                </div>
                <StopRunButton runId={r.id} name={r.strategy_name} />
              </div>
            </li>
          ))}
        </ul>
      )}

      {risk.ok && <RiskSettingsCard initial={risk.data} />}

      {history.ok && history.data.length > 0 && (
        <Card className="flex flex-col gap-2">
          <CardTitle>Recently stopped</CardTitle>
          <ul className="divide-y divide-border text-sm">
            {history.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <Link href={`/runs/${r.id}`} className="hover:underline">
                  {r.strategy_name} <span className="text-muted">· {fmt(r.stopped_at)}</span>
                </Link>
                <span className="flex items-center gap-3">
                  <span className="text-xs text-muted">{r.error ?? r.stop_reason}</span>
                  <Pnl value={r.realized_pnl} />
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
