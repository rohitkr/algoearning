import type { Backtest, HistoryCoverage, StrategyPage } from "@algoearning/api-types";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import Link from "next/link";

import { NewBacktest } from "@/components/backtesting/new-backtest";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Backtesting" };

const TONE = { pending: "info", running: "info", done: "success", error: "danger" } as const;
const dt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

export default async function BacktestingPage() {
  await requireUser();
  const [list, strategies, coverage] = await Promise.all([
    apiGet<Backtest[]>("/v1/backtests"),
    apiGet<StrategyPage>("/v1/strategies?limit=100&status=draft&status=ready"),
    apiGet<HistoryCoverage[]>("/v1/backtests/coverage"),
  ]);
  const waiting = list.ok && list.data.some((b) => b.status === "pending" || b.status === "running");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      {waiting && <AutoRefresh seconds={3} />}
      <div>
        <h1 className="text-2xl font-semibold">Backtesting</h1>
        <p className="text-sm text-muted">
          Replay a strategy over stored 1-minute history with the same rules that run live.
        </p>
      </div>
      {strategies.ok && coverage.ok ? (
        <NewBacktest strategies={strategies.data.items} coverage={coverage.data} />
      ) : (
        <StatusPill tone="danger">Could not load your strategies</StatusPill>
      )}
      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Your backtests</CardTitle>
        </div>
        {!list.ok ? (
          <p className="px-5 pb-5 text-sm text-loss">{list.error.message}</p>
        ) : list.data.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">None yet.</p>
        ) : (
          <ul className="divide-y divide-border px-5 pb-2 text-sm">
            {list.data.map((b) => (
              <li key={b.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span className="min-w-0">
                  <Link href={`/backtesting/${b.id}`} className="font-medium hover:underline">
                    {b.strategy_name}
                  </Link>
                  <span className="block text-xs text-muted">
                    {b.underlying} · {b.start_date} to {b.end_date} · ×{b.multiplier} ·{" "}
                    {dt.format(new Date(b.created_at))}
                  </span>
                </span>
                <span className="flex items-center gap-3">
                  {b.status === "done" ? (
                    <>
                      <span className="text-xs text-muted">{b.trades} trades</span>
                      <Pnl value={b.net_pnl} className="font-semibold" />
                    </>
                  ) : (
                    <StatusPill tone={TONE[b.status]}>
                      {b.status === "error" ? "Failed" : b.status === "running" ? "Running" : "Queued"}
                    </StatusPill>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
