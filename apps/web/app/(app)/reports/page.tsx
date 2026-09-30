import type {
  ReportDay,
  ReportSummary,
  StrategyPage,
  StrategyPerformance,
  TradePage,
} from "@algoearning/api-types";
import { formatNumber, formatPnl } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill, cn } from "@algoearning/ui";
import Link from "next/link";

import { inputClass } from "@/components/builder/fields";
import { CsvButton } from "@/components/reports/csv-button";
import { DailyPnlBars, EquityCurve } from "@/components/reports/pnl-charts";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Reports" };

const iso = (d: Date) => d.toISOString().slice(0, 10);
const dt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
const fmt = (v: string | null | undefined) => (v ? dt.format(new Date(v)) : "–");
const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);

function Tile({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <div className="mt-1 text-xl font-semibold tabular-nums">{children}</div>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Card>
  );
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireUser();
  const sp = await searchParams;
  // the range is resolved by the API when absent (last 30 days, IST); presets are computed here for the links
  const q = new URLSearchParams();
  if (sp.from) q.set("from", sp.from);
  if (sp.to) q.set("to", sp.to);
  if (sp.mode) q.set("mode", sp.mode);
  if (sp.strategy_id) q.set("strategy_id", sp.strategy_id);
  const trades = new URLSearchParams(q);
  if (sp.cursor) trades.set("cursor", sp.cursor);
  const [summary, days, perf, log, strategies] = await Promise.all([
    apiGet<ReportSummary>(`/v1/reports/summary?${q}`),
    apiGet<ReportDay[]>(`/v1/reports/daily?${q}`),
    apiGet<StrategyPerformance[]>(
      `/v1/reports/strategies?${new URLSearchParams([...q].filter(([k]) => k !== "strategy_id"))}`,
    ),
    apiGet<TradePage>(`/v1/reports/trades?${trades}&limit=25`),
    apiGet<StrategyPage>("/v1/strategies?limit=100"),
  ]);
  if (!summary.ok)
    return <StatusPill tone="danger">Could not load reports: {summary.error.message}</StatusPill>;
  const s = summary.data;
  const today = new Date(`${s.to_date}T00:00:00Z`);
  const preset = (n: number) => {
    const p = new URLSearchParams(q);
    p.set("to", s.to_date);
    p.set("from", iso(new Date(today.getTime() - (n - 1) * 86_400_000)));
    p.delete("cursor");
    return `/reports?${p}`;
  };
  const more = new URLSearchParams(trades);
  if (log.ok && log.data.next_cursor) more.set("cursor", log.data.next_cursor);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Reports</h1>
          <p className="text-sm text-muted">
            Closed trades from {s.from_date} to {s.to_date}, counted on the day they closed.
          </p>
        </div>
        <CsvButton query={q.toString()} />
      </div>

      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-1 text-sm">
          {[
            [7, "7 days"],
            [30, "30 days"],
            [90, "90 days"],
            [365, "1 year"],
          ].map(([n, label]) => (
            <Link
              key={label}
              href={preset(Number(n))}
              className="rounded-lg px-3 py-1.5 text-muted hover:bg-surface-2"
            >
              {label}
            </Link>
          ))}
        </div>
        <form className="flex flex-wrap items-end gap-2" action="/reports">
          <label className="flex flex-col gap-1 text-xs font-medium text-muted">
            From
            <input type="date" name="from" defaultValue={s.from_date} className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted">
            To
            <input type="date" name="to" defaultValue={s.to_date} className={inputClass} />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted">
            Mode
            <select name="mode" defaultValue={sp.mode ?? ""} className={inputClass}>
              <option value="">Paper and live</option>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label className="flex min-w-48 flex-col gap-1 text-xs font-medium text-muted">
            Strategy
            <select name="strategy_id" defaultValue={sp.strategy_id ?? ""} className={inputClass}>
              <option value="">All strategies</option>
              {strategies.ok &&
                strategies.data.items.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="submit"
            className="h-9 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            Apply
          </button>
        </form>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Total P&L" sub={`${s.trades} trades · ${s.trading_days} days`}>
          <Pnl value={s.total_pnl} />
        </Tile>
        <Tile label="Win rate" sub={`${s.wins} won · ${s.losses} lost`}>
          {pct(s.win_rate)}
        </Tile>
        <Tile
          label="Average win / loss"
          sub={s.profit_factor ? `profit factor ${s.profit_factor}` : undefined}
        >
          <span className="text-base">
            <Pnl value={s.avg_win} /> / <Pnl value={s.avg_loss} />
          </span>
        </Tile>
        <Tile
          label="Max drawdown"
          sub={
            s.best_day
              ? `best ${formatPnl(s.best_day.pnl)} · worst ${formatPnl(s.worst_day?.pnl ?? 0)}`
              : undefined
          }
        >
          <Pnl value={s.max_drawdown} />
        </Tile>
      </div>

      {days.ok && days.data.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardTitle>Daily P&amp;L</CardTitle>
            <div className="mt-3">
              <DailyPnlBars days={days.data} />
            </div>
          </Card>
          <Card>
            <CardTitle>Cumulative P&amp;L</CardTitle>
            <div className="mt-3">
              <EquityCurve days={days.data} />
            </div>
          </Card>
          <details className="lg:col-span-2">
            <summary className="cursor-pointer text-sm text-muted">Show the daily numbers as a table</summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left text-sm tabular-nums">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    <th className="px-3 py-2 font-medium">Day</th>
                    <th className="px-3 py-2 font-medium">Trades</th>
                    <th className="px-3 py-2 font-medium">P&amp;L</th>
                    <th className="px-3 py-2 font-medium">Cumulative</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {days.data.map((d) => (
                    <tr key={d.day}>
                      <td className="px-3 py-2">{d.day}</td>
                      <td className="px-3 py-2">{d.trades}</td>
                      <td className="px-3 py-2">
                        <Pnl value={d.pnl} />
                      </td>
                      <td className="px-3 py-2">
                        <Pnl value={d.cumulative} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      ) : (
        <Card className="text-center text-sm text-muted">
          No closed trades in this range yet. Deploy a strategy on paper and results appear here.
        </Card>
      )}

      {perf.ok && perf.data.length > 0 && (
        <Card className="p-0">
          <div className="px-5 pt-4 pb-2">
            <CardTitle>By strategy</CardTitle>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[32rem] text-left text-sm tabular-nums">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  {["Strategy", "Runs", "Trades", "Win rate", "P&L"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {perf.data.map((p) => (
                  <tr key={p.strategy_id ?? "manual"}>
                    <td className="px-3 py-2.5">{p.strategy_name}</td>
                    <td className="px-3 py-2.5">{p.runs}</td>
                    <td className="px-3 py-2.5">{p.trades}</td>
                    <td className="px-3 py-2.5">{pct(p.win_rate)}</td>
                    <td className="px-3 py-2.5">
                      <Pnl value={p.pnl} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {log.ok && log.data.items.length > 0 && (
        <Card className="p-0">
          <div className="px-5 pt-4 pb-2">
            <CardTitle>Trade log</CardTitle>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] text-left text-sm tabular-nums">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  {["Closed", "Strategy", "Contract", "Side", "Qty", "Entry", "Exit", "Reason", "P&L"].map(
                    (h) => (
                      <th key={h} className="px-3 py-2 font-medium whitespace-nowrap">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {log.data.items.map((t) => (
                  <tr key={t.id}>
                    <td className="px-3 py-2.5 whitespace-nowrap text-muted">{fmt(t.exit_time)}</td>
                    <td className="px-3 py-2.5">
                      {t.run_id ? (
                        <Link href={`/runs/${t.run_id}`} className="hover:underline">
                          {t.strategy_name}
                        </Link>
                      ) : (
                        t.strategy_name
                      )}
                      {t.mode === "paper" && <span className="ml-1 text-xs text-muted">paper</span>}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {t.underlying} {t.strike} {t.option_type}
                    </td>
                    <td className={cn("px-3 py-2.5", t.side === "BUY" ? "text-profit" : "text-loss")}>
                      {t.side}
                    </td>
                    <td className="px-3 py-2.5">{t.quantity}</td>
                    <td className="px-3 py-2.5">{formatNumber(t.entry_price)}</td>
                    <td className="px-3 py-2.5">{formatNumber(t.exit_price)}</td>
                    <td className="px-3 py-2.5 text-xs text-muted">{t.exit_reason}</td>
                    <td className="px-3 py-2.5">
                      <Pnl value={t.pnl} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(sp.cursor || log.data.next_cursor) && (
            <div className="flex justify-between px-5 py-3 text-sm">
              {sp.cursor ? (
                <Link href={`/reports?${q}`} className="text-primary-text underline">
                  Newest
                </Link>
              ) : (
                <span />
              )}
              {log.data.next_cursor && (
                <Link href={`/reports?${more}`} className="text-primary-text underline">
                  Older
                </Link>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
