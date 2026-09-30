import type {
  BrokerAccount,
  Entitlements,
  OpenPosition,
  ReportDay,
  ReportSummary,
  Run,
} from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Button, Card, CardTitle, Meter, Pnl, StatusPill } from "@algoearning/ui";
import Link from "next/link";

import { AccountCard } from "@/components/account-card";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { ModePill, RunStatusPill } from "@/components/runs/run-bits";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Dashboard" };

const USAGE: Record<string, string> = {
  max_strategies: "Saved strategies",
  max_running_strategies: "Running strategies",
  max_broker_accounts: "Broker accounts",
};

export default async function DashboardPage() {
  await requireUser();
  const [runs, month, days, positions, brokers, ent] = await Promise.all([
    apiGet<Run[]>("/v1/runs"),
    apiGet<ReportSummary>("/v1/reports/summary"),
    apiGet<ReportDay[]>("/v1/reports/daily"),
    apiGet<OpenPosition[]>("/v1/positions/open"),
    apiGet<BrokerAccount[]>("/v1/broker-accounts"),
    apiGet<Entitlements>("/v1/me/entitlements"),
  ]);
  const active = runs.ok ? runs.data : [];
  const open = positions.ok ? positions.data : [];
  // today = the last day of the default report range (the API resolves it in IST)
  const bookedToday =
    month.ok && days.ok ? (days.data.find((d) => d.day === month.data.to_date)?.pnl ?? 0) : 0;
  const openPnl = active.reduce((a, r) => a + r.unrealized_pnl, 0);
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <AutoRefresh seconds={5} />
      <h1 className="text-2xl font-semibold">My Dashboard</h1>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="col-span-2 bg-primary text-primary-foreground lg:col-span-1">
          <p className="text-sm opacity-80">Today&apos;s P&amp;L</p>
          <p className="mt-1 text-3xl font-semibold tabular-nums">
            {(bookedToday + openPnl >= 0 ? "+" : "") + formatNumber(bookedToday + openPnl)}
          </p>
          <p className="mt-1 text-xs opacity-80">
            ₹{formatNumber(bookedToday)} booked · ₹{formatNumber(openPnl)} open
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Last 30 days</p>
          <p className="mt-1 text-xl font-semibold">
            <Pnl value={month.ok ? month.data.total_pnl : null} />
          </p>
          <Link href="/reports" className="text-xs text-primary-text underline">
            Reports
          </Link>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Running strategies</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{active.length}</p>
          <Link href="/runs" className="text-xs text-primary-text underline">
            Manage
          </Link>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted">Open positions</p>
          <p className="mt-1 text-xl font-semibold tabular-nums">{open.length}</p>
          <p className="text-xs text-muted">
            {open.length ? `${open.reduce((a, p) => a + p.quantity, 0)} qty` : "flat"}
          </p>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <CardTitle>Strategies running</CardTitle>
            <Link href="/strategies" className="text-sm text-primary-text underline">
              Deploy
            </Link>
          </div>
          {active.length === 0 ? (
            <p className="text-sm text-muted">Nothing running. Deploy a ready strategy on paper to start.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-border text-sm">
              {active.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 py-2">
                  <span className="flex min-w-0 flex-col items-start gap-1">
                    <Link href={`/runs/${r.id}`} className="truncate font-medium hover:underline">
                      {r.strategy_name}
                    </Link>
                    <span className="flex gap-1">
                      <ModePill mode={r.mode} dryRun={r.dry_run} />
                      <RunStatusPill run={r} />
                    </span>
                  </span>
                  <Pnl value={r.realized_pnl + r.unrealized_pnl} className="font-semibold" />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="flex flex-col gap-3">
          <CardTitle>Open positions</CardTitle>
          {open.length === 0 ? (
            <p className="text-sm text-muted">No open positions.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[26rem] text-left text-sm tabular-nums">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    <th className="py-1.5 pr-3 font-medium">Contract</th>
                    <th className="py-1.5 pr-3 font-medium">Qty</th>
                    <th className="py-1.5 pr-3 font-medium">Entry</th>
                    <th className="py-1.5 pr-3 font-medium">LTP</th>
                    <th className="py-1.5 font-medium">P&amp;L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {open.map((p) => (
                    <tr key={p.id}>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <span className={p.side === "BUY" ? "text-profit" : "text-loss"}>
                          {p.side === "BUY" ? "B" : "S"}
                        </span>{" "}
                        {p.underlying} {p.strike} {p.option_type}
                      </td>
                      <td className="py-2 pr-3">{p.quantity}</td>
                      <td className="py-2 pr-3">{formatNumber(p.entry_price)}</td>
                      <td className="py-2 pr-3">{formatNumber(p.last_ltp)}</td>
                      <td className="py-2">
                        <Pnl value={p.pnl} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <CardTitle>Brokers</CardTitle>
            <Link href="/brokers" className="text-sm text-primary-text underline">
              Manage
            </Link>
          </div>
          {brokers.ok && brokers.data.length > 0 ? (
            <ul className="flex flex-col gap-2 text-sm">
              {brokers.data.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">
                    {b.broker_name} · {b.client_id}
                  </span>
                  <StatusPill
                    tone={
                      b.status === "connected" ? "success" : b.status === "expired" ? "warning" : "danger"
                    }
                  >
                    {b.status === "connected"
                      ? "Connected"
                      : b.status === "expired"
                        ? "Log in again"
                        : "Not connected"}
                  </StatusPill>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-start gap-2 text-sm text-muted">
              No broker yet. Paper trading works without one.
              <Button asChild size="sm" variant="secondary">
                <Link href="/brokers">Add broker</Link>
              </Button>
            </div>
          )}
        </Card>
        <Card className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <CardTitle>Plan</CardTitle>
            {ent.ok && (
              <StatusPill tone={ent.data.plan_code === "free" ? "neutral" : "info"}>
                {ent.data.plan_name}
              </StatusPill>
            )}
          </div>
          {ent.ok &&
            Object.entries(ent.data.usage).map(([k, u]) => (
              <Meter key={k} label={USAGE[k] ?? k} used={u.used} limit={u.limit ?? null} />
            ))}
        </Card>
        <AccountCard />
      </div>
    </div>
  );
}
