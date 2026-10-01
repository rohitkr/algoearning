import type { BacktestDetail } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import { ArrowLeft, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DeleteBacktest } from "@/components/backtesting/delete-backtest";
import { DailyPnlBars, EquityCurve } from "@/components/reports/pnl-charts";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { SignalCard, type SmcSignal } from "@/components/smc/signal-card";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Backtest" };

type Trade = {
  leg: string;
  contract: string;
  side: string;
  qty: number;
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  reason: string;
  gross: number;
  charges: number;
  net: number;
};
type Day = { day: string; pnl: number; trades: number; cumulative: number };
type Signals = {
  count: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  max_consecutive_losses: number;
  avg_holding_minutes: number | null;
  funnel: Record<string, number>;
  list: (SmcSignal & { time: string })[];
};
type Summary = Record<string, number | null | { day: string; pnl: number }>;

const dt = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const pct = (v: unknown) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "–");
const n = (v: unknown) => (typeof v === "number" ? v : null);

function Tile({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <div className="mt-1 text-xl font-semibold tabular-nums">{children}</div>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Card>
  );
}

export default async function BacktestPage({ params }: { params: Promise<{ id: string }> }) {
  await requireUser();
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const r = await apiGet<BacktestDetail>(`/v1/backtests/${id}`);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok) return <StatusPill tone="danger">Could not load the backtest: {r.error.message}</StatusPill>;
  const b = r.data;
  const res = b.result as {
    summary: Summary;
    daily: Day[];
    trades: Trade[];
    trades_total: number;
    warnings: string[];
    signals?: Signals | null;
  } | null;
  const sig = res?.signals;
  const s = res?.summary;
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

      {res && s && (
        <>
          {res.warnings.length > 0 && (
            <Card className="flex flex-col gap-1 border-warning/40 bg-warning/10">
              {res.warnings.map((w, i) => (
                <p key={`${i}-${w}`} className="flex items-start gap-2 text-sm text-warning">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> {w}
                </p>
              ))}
            </Card>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile
              label="Net P&L"
              sub={`gross ${formatNumber(n(s.gross_pnl))} · charges ${formatNumber(n(s.charges))}`}
            >
              <Pnl value={n(s.net_pnl)} />
            </Tile>
            <Tile label="Win rate" sub={`${s.wins} won · ${s.losses} lost · ${s.trades} trades`}>
              {pct(s.win_rate)}
            </Tile>
            <Tile
              label="Average win / loss"
              sub={s.profit_factor ? `profit factor ${s.profit_factor}` : undefined}
            >
              <span className="text-base">
                <Pnl value={n(s.avg_win)} /> / <Pnl value={n(s.avg_loss)} />
              </span>
            </Tile>
            <Tile label="Max drawdown" sub={`${s.trading_days} trading days of ${s.days_replayed} replayed`}>
              <Pnl value={n(s.max_drawdown)} />
            </Tile>
            <Tile label="Expectancy" sub="average net P&L per trade">
              <Pnl value={n(s.expectancy)} />
            </Tile>
            <Tile label="Max consecutive losses">{n(s.max_consecutive_losses) ?? "–"}</Tile>
            <Tile label="Average holding time">
              {n(s.avg_holding_minutes) == null ? "–" : `${s.avg_holding_minutes} min`}
            </Tile>
            <Tile label="Charges" sub="as a share of gross profit">
              {n(s.charges_pct_of_gross) == null ? "–" : `${s.charges_pct_of_gross}%`}
            </Tile>
          </div>
          {sig && (
            <Card className="flex flex-col gap-4">
              <div>
                <CardTitle>Signals</CardTitle>
                <p className="text-sm text-muted">
                  Each signal&apos;s tranches counted as one trade idea. Win rate here is per signal; the
                  tiles above count every tranche.
                </p>
              </div>
              <dl className="grid grid-cols-2 gap-3 text-sm tabular-nums sm:grid-cols-4">
                {(
                  [
                    ["Signals", sig.count],
                    ["Win rate", pct(sig.win_rate)],
                    ["Profit factor", sig.profit_factor ?? "–"],
                    ["Expectancy", sig.expectancy == null ? "–" : formatNumber(sig.expectancy)],
                    ["Average win", sig.avg_win == null ? "–" : formatNumber(sig.avg_win)],
                    ["Average loss", sig.avg_loss == null ? "–" : formatNumber(sig.avg_loss)],
                    ["Max consecutive losses", sig.max_consecutive_losses],
                    [
                      "Average holding",
                      sig.avg_holding_minutes == null ? "–" : `${sig.avg_holding_minutes} min`,
                    ],
                  ] as const
                ).map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-xs text-muted">{k}</dt>
                    <dd className="font-semibold">{v}</dd>
                  </div>
                ))}
              </dl>
              {Object.keys(sig.funnel).length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted">Why setups did or did not become trades</p>
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {Object.entries(sig.funnel)
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, v]) => (
                        <li key={k}>
                          <StatusPill>
                            {k} · {v}
                          </StatusPill>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
              {sig.list.slice(0, 50).map((x) => (
                <SignalCard key={x.time} s={x} when={dt.format(new Date(x.time))} />
              ))}
            </Card>
          )}
          {res.daily.length > 0 && (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardTitle>Daily P&amp;L</CardTitle>
                <div className="mt-3">
                  <DailyPnlBars days={res.daily} />
                </div>
              </Card>
              <Card>
                <CardTitle>Cumulative P&amp;L</CardTitle>
                <div className="mt-3">
                  <EquityCurve days={res.daily} />
                </div>
              </Card>
            </div>
          )}
          <Card className="p-0">
            <div className="px-5 pt-4 pb-2">
              <CardTitle>
                Trades{" "}
                {res.trades_total > res.trades.length &&
                  `(first ${res.trades.length} of ${res.trades_total})`}
              </CardTitle>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[56rem] text-left text-sm tabular-nums">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    {[
                      "Entered",
                      "Contract",
                      "Side",
                      "Qty",
                      "Entry",
                      "Exit",
                      "Reason",
                      "Charges",
                      "Net P&L",
                    ].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {res.trades.slice(0, 300).map((t, i) => (
                    <tr key={i}>
                      <td className="px-3 py-2 whitespace-nowrap text-muted">
                        {dt.format(new Date(t.entry_time))}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{t.contract}</td>
                      <td className={t.side === "BUY" ? "px-3 py-2 text-profit" : "px-3 py-2 text-loss"}>
                        {t.side}
                      </td>
                      <td className="px-3 py-2">{t.qty}</td>
                      <td className="px-3 py-2">{formatNumber(t.entry_price)}</td>
                      <td className="px-3 py-2">{formatNumber(t.exit_price)}</td>
                      <td className="px-3 py-2 text-xs text-muted">{t.reason}</td>
                      <td className="px-3 py-2 text-muted">{formatNumber(t.charges)}</td>
                      <td className="px-3 py-2">
                        <Pnl value={t.net} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <p className="text-xs text-muted">
            Fills use each minute&apos;s open (stops and targets fill at their level, or the open if the price
            gapped past them; a stop wins when both were reachable inside a minute), with your slippage and
            approximate charges. Past results do not predict future ones.
          </p>
        </>
      )}
    </div>
  );
}
