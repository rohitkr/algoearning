import type { RunDetail, Strategy } from "@algoearning/api-types";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/runs/auto-refresh";
import { EventsLog } from "@/components/runs/events-log";
import { PositionsTable } from "@/components/runs/positions-table";
import { LegsPreview } from "@/components/strategies/legs-preview";
import {
  EnterNowButton,
  ExitNowButton,
  PauseRunButton,
  ReleaseRunButton,
  StopRunButton,
} from "@/components/runs/run-actions";
import { ModePill, PricesAsOf, RunStatusPill, StalePricesNotice, fmt } from "@/components/runs/run-bits";
import { SignalCard, type SmcSignal, isSmcSignal } from "@/components/smc/signal-card";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Run" };

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const r = await apiGet<RunDetail>(`/v1/runs/${id}`);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <StatusPill tone="danger">Could not load the run: {r.error.message}</StatusPill>;
  const { run, positions, events } = r.data;
  const active = ["pending", "running", "stopping"].includes(run.status);
  const signals = events.filter((e) => isSmcSignal(e.event));
  // with no trade open, the legs it would trade now (from the saved strategy, at this run's multiplier)
  const strat =
    active && run.open_positions === 0 ? await apiGet<Strategy>(`/v1/strategies/${run.strategy_id}`) : null;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      {active && <AutoRefresh />}
      <Link href="/runs" className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:underline">
        <ArrowLeft className="size-4" aria-hidden /> Running
      </Link>
      {active && <StalePricesNotice run={run} />}
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
            {active && <PricesAsOf run={run} />}
          </div>
          <div>
            <p className="text-xs text-muted">Total</p>
            <Pnl value={run.realized_pnl + run.unrealized_pnl} className="text-lg font-semibold" />
          </div>
        </div>
        {/* one button: Exit now while a trade is open, Enter now when none is */}
        {run.status === "running" &&
          (run.open_positions > 0 ? (
            <ExitNowButton runId={run.id} name={run.strategy_name} live={run.mode === "live"} />
          ) : (
            // with the legs preview below, Enter now lives there and trades exactly the legs shown
            !strat?.ok && (
              <EnterNowButton runId={run.id} name={run.strategy_name} live={run.mode === "live"} />
            )
          ))}
        {(run.status === "running" || run.status === "pending") && (
          <PauseRunButton runId={run.id} name={run.strategy_name} paused={run.paused ?? false} />
        )}
        {run.status === "stopping" && (
          <ReleaseRunButton runId={run.id} name={run.strategy_name} stopping={run.status === "stopping"} />
        )}
        {active && run.status !== "stopping" && (
          <StopRunButton runId={run.id} name={run.strategy_name} open={run.open_positions} />
        )}
      </Card>

      <PositionsTable positions={positions} />
      {strat?.ok && (
        <LegsPreview
          config={strat.data.config}
          multiplier={run.multiplier}
          runId={run.status === "running" ? run.id : undefined}
          live={run.mode === "live" && !run.dry_run}
          brokerAccountId={run.broker_account_id}
        />
      )}

      {signals.length > 0 && (
        <Card className="flex flex-col gap-3">
          <CardTitle>Signals</CardTitle>
          {signals.slice(0, 20).map((e) => (
            <SignalCard key={e.id} s={e.detail as SmcSignal} when={fmt(e.ts)} />
          ))}
        </Card>
      )}

      <EventsLog events={events} />
    </div>
  );
}
