import type { RunDetail } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/runs/auto-refresh";
import { StopRunButton } from "@/components/runs/run-actions";
import { ModePill, RunStatusPill, fmt } from "@/components/runs/run-bits";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Run" };

const th = "px-3 py-2 font-medium whitespace-nowrap";
const td = "px-3 py-2.5 whitespace-nowrap";
const num = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v));

function describe(detail: Record<string, unknown>): string {
  const skip = new Set(["trade_id"]);
  return Object.entries(detail)
    .filter(([k, v]) => !skip.has(k) && v != null && v !== "")
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const r = await apiGet<RunDetail>(`/v1/runs/${id}`);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <StatusPill tone="danger">Could not load the run: {r.error.message}</StatusPill>;
  const { run, positions, events } = r.data;
  const active = ["pending", "running", "stopping"].includes(run.status);
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      {active && <AutoRefresh />}
      <Link href="/runs" className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:underline">
        <ArrowLeft className="size-4" aria-hidden /> Running
      </Link>
      <Card className="flex flex-wrap items-center gap-4">
        <div className="min-w-48 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">{run.strategy_name}</h1>
            <ModePill mode={run.mode} dryRun={run.dry_run} />
            <RunStatusPill run={run} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {run.underlying} · ×{run.multiplier} · started {fmt(run.started_at)}
            {run.stopped_at && ` · stopped ${fmt(run.stopped_at)}`}
          </p>
          {(run.error || run.stop_reason) && (
            <p className={run.error ? "mt-1 text-sm text-loss" : "mt-1 text-sm text-muted"}>
              {run.error ?? run.stop_reason}
            </p>
          )}
        </div>
        <div className="grid grid-cols-3 gap-6 text-right">
          <div>
            <p className="text-xs text-muted">Booked</p>
            <Pnl value={run.realized_pnl} className="font-semibold" />
          </div>
          <div>
            <p className="text-xs text-muted">Open</p>
            <Pnl value={run.unrealized_pnl} className="font-semibold" />
          </div>
          <div>
            <p className="text-xs text-muted">Total</p>
            <Pnl value={run.realized_pnl + run.unrealized_pnl} className="text-lg font-semibold" />
          </div>
        </div>
        {active && run.status !== "stopping" && <StopRunButton runId={run.id} name={run.strategy_name} />}
      </Card>

      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Positions</CardTitle>
        </div>
        {positions.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">
            No positions yet. The strategy enters at its entry time.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  {["Contract", "Side", "Qty", "Entry", "LTP / exit", "SL", "Target", "P&L", "Status"].map(
                    (h) => (
                      <th key={h} scope="col" className={th}>
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border tabular-nums">
                {positions.map((p) => (
                  <tr key={p.id}>
                    <td className={td}>
                      <p className="font-medium">
                        {p.underlying} {p.strike} {p.option_type}
                      </p>
                      <p className="text-xs text-muted">
                        {p.expiry} · {p.leg}
                      </p>
                    </td>
                    <td className={td}>
                      <StatusPill tone={p.side === "BUY" ? "success" : "danger"}>{p.side}</StatusPill>
                    </td>
                    <td className={td}>
                      {p.quantity} <span className="text-xs text-muted">({p.lots} lots)</span>
                    </td>
                    <td className={td}>{num(p.entry_price)}</td>
                    <td className={td}>{num(p.status === "open" ? p.last_ltp : p.exit_price)}</td>
                    <td className={td}>{num(p.current_sl)}</td>
                    <td className={td}>{num(p.target)}</td>
                    <td className={td}>
                      <Pnl value={p.pnl} />
                    </td>
                    <td className={td}>
                      {p.status === "open" ? (
                        <StatusPill tone="success">open</StatusPill>
                      ) : (
                        <span className="text-xs text-muted">{p.exit_reason}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>What happened</CardTitle>
        </div>
        {events.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">Nothing yet.</p>
        ) : (
          <ul className="divide-y divide-border px-5 pb-3 text-sm">
            {events.map((e) => (
              <li key={e.id} className="flex flex-wrap gap-x-3 gap-y-0.5 py-2">
                <span className="text-xs whitespace-nowrap text-muted tabular-nums">{fmt(e.ts)}</span>
                <span
                  className={
                    e.level === "ERROR"
                      ? "font-medium text-loss"
                      : e.level === "WARNING"
                        ? "font-medium text-warning"
                        : "font-medium"
                  }
                >
                  {e.event.replaceAll("_", " ")}
                </span>
                <span className="min-w-0 text-muted">{describe(e.detail)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
