import type { BacktestDetail } from "@algoearning/api-types";
import { Card, StatusPill } from "@algoearning/ui";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DeleteBacktest } from "@/components/backtesting/delete-backtest";
import { BacktestResultView, type BacktestResultData } from "@/components/backtesting/result-view";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Backtest" };

export default async function BacktestPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const r = await apiGet<BacktestDetail>(`/v1/backtests/${id}`);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <StatusPill tone="danger">Could not load the backtest: {r.error.message}</StatusPill>;
  const b = r.data;
  const res = b.result as BacktestResultData | null;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      {(b.status === "pending" || b.status === "running") && <AutoRefresh seconds={2} />}
      <Link
        href="/backtesting"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Backtesting
      </Link>
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{b.strategy_name}</h1>
          <p className="text-sm text-muted">
            {b.underlying} · {b.start_date} to {b.end_date} · ×{b.multiplier} · slippage {b.slippage_pct}%
          </p>
        </div>
        <span className="flex items-center gap-3">
          <StatusPill tone={b.status === "done" ? "success" : b.status === "error" ? "danger" : "info"}>
            {b.status === "done"
              ? "Done"
              : b.status === "error"
                ? "Failed"
                : b.status === "running"
                  ? "Running…"
                  : "Queued…"}
          </StatusPill>
          <DeleteBacktest id={b.id} />
        </span>
      </Card>

      {b.status === "error" && <p className="text-sm text-loss">{b.error}</p>}
      {b.status !== "done" && b.status !== "error" && (
        <p className="text-sm text-muted">
          The worker is replaying your strategy. This page updates by itself.
        </p>
      )}

      {res && <BacktestResultView res={res} />}
    </div>
  );
}
