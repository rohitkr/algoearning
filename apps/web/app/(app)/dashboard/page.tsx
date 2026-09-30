import { Card, CardTitle, Pnl, StatusPill, Switch } from "@algoearning/ui";

import { AccountCard } from "@/components/account-card";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Dashboard" };

/** The account card is live (phase 4); the other cards are sample values until phase 11 wires real data. */
export default async function DashboardPage() {
  await requireUser();
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">My Dashboard</h1>
        <StatusPill tone="info">Preview · sample data</StatusPill>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card className="bg-primary text-primary-foreground">
          <p className="text-sm opacity-80">Total P&amp;L</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">₹12,450.00</p>
          <p className="mt-1 text-sm opacity-80">Today, across all brokers</p>
        </Card>
        <Card>
          <CardTitle>Broker</CardTitle>
          <p className="mt-2 font-semibold">Zerodha · AB1234</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusPill tone="danger">Not connected</StatusPill>
            <StatusPill>Static IP: not assigned</StatusPill>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm">
            <label className="flex items-center gap-2 whitespace-nowrap">
              <Switch aria-label="Terminal" /> Terminal
            </label>
            <label className="flex items-center gap-2 whitespace-nowrap">
              <Switch aria-label="Trading engine" /> Trading Engine
            </label>
          </div>
        </Card>
        <AccountCard />
        <Card>
          <CardTitle>Strategies deployed</CardTitle>
          <ul className="mt-3 flex flex-col gap-3 text-sm">
            <li className="flex items-center justify-between gap-3">
              <span className="flex flex-col items-start gap-1">
                Short Straddle NIFTY <StatusPill tone="success">Running</StatusPill>
              </span>
              <Pnl value={3250} />
            </li>
            <li className="flex items-center justify-between gap-3">
              <span className="flex flex-col items-start gap-1">
                Iron Condor BANKNIFTY <StatusPill tone="warning">Paper</StatusPill>
              </span>
              <Pnl value={-820.5} />
            </li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
