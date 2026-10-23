"use client";

import type { RunDetail } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill, cn } from "@algoearning/ui";
import { useEffect, useState } from "react";

type Position = RunDetail["positions"][number];

const th = "px-3 py-2 font-medium whitespace-nowrap";
const td = "px-3 py-2.5 whitespace-nowrap";
const num = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v));
const ist = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }); // yyyy-mm-dd
const dayOf = (t: string | null | undefined) => (t ? ist.format(new Date(t)) : null);

type Filter = "current" | "open" | "all";
const RUNNING_ONLY_KEY = "ae_positions_running_only";
const RANGE_KEY = "ae_positions_range";

/** A position belongs in "current" while it is open (a trade held overnight included) or when it was opened or
 * closed today; earlier days' closed trades are one click away under "All". */
export function currentPositions(
  positions: readonly Position[],
  filter: Filter,
  now = new Date(),
): Position[] {
  if (filter === "all") return [...positions];
  const today = ist.format(now);
  const open = positions.filter((p) => p.status === "open");
  if (filter === "open") return open;
  return positions.filter(
    (p) => p.status === "open" || dayOf(p.exit_time) === today || dayOf(p.entry_time) === today,
  );
}

/** The positions of the days `from`..`to` (yyyy-mm-dd, IST; "" = today): opened or closed in that range, plus the
 * open ones when the range reaches today (a trade held overnight is still today's business). */
export function positionsBetween(
  positions: readonly Position[],
  from: string,
  to: string,
  now = new Date(),
): Position[] {
  const today = ist.format(now);
  const a = from || today;
  const b = to || today;
  const lo = a <= b ? a : b;
  const hi = a <= b ? b : a;
  const inRange = (t: string | null | undefined) => {
    const d = dayOf(t);
    return d !== null && d >= lo && d <= hi;
  };
  return positions.filter(
    (p) => (p.status === "open" && hi >= today) || inRange(p.entry_time) || inRange(p.exit_time),
  );
}

const time = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const dayTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
/** "10:12" today, "22 Oct, 10:12" another day. */
function when(t: string | null | undefined, now = new Date()): string {
  if (!t) return "";
  const d = new Date(t);
  return dayOf(t) === ist.format(now) ? time.format(d) : dayTime.format(d);
}

const isQuarter = (p: Position) => /^T[1-4]$/.test(p.leg ?? "");

/** A Telegram tip traded as given is bought as quarters (legs T1-T4) that leave at different targets: they are one
 * trade, shown together. Everything else stays one row per position. */
export function groupTipTrades(positions: readonly Position[]): Position[][] {
  const out: Position[][] = [];
  for (const p of positions) {
    const g = isQuarter(p)
      ? out.find(
          (x) =>
            isQuarter(x[0]!) &&
            x[0]!.tradingsymbol === p.tradingsymbol &&
            x[0]!.side === p.side &&
            Math.abs(Date.parse(x[0]!.entry_time ?? "") - Date.parse(p.entry_time ?? "")) < 120_000,
        )
      : undefined;
    if (g) g.push(p);
    else out.push([p]);
  }
  for (const g of out) g.sort((a, b) => (a.leg ?? "").localeCompare(b.leg ?? ""));
  return out;
}

function Priced({ price, at }: { price: number | null | undefined; at?: string | null }) {
  return (
    <>
      {num(price)}
      {at && <p className="text-xs text-muted">{when(at)}</p>}
    </>
  );
}

function PositionRow({ p, sub = false }: { p: Position; sub?: boolean }) {
  return (
    <tr className={sub ? "text-muted" : undefined}>
      <td className={cn(td, sub && "pl-8")}>
        {sub ? (
          <p className="text-xs">↳ {p.leg}</p>
        ) : (
          <>
            <p className="font-medium">
              {p.underlying} {p.strike} {p.option_type}
            </p>
            <p className="text-xs text-muted">
              {p.expiry} · {p.leg}
            </p>
          </>
        )}
      </td>
      <td className={td}>
        {!sub && <StatusPill tone={p.side === "BUY" ? "success" : "danger"}>{p.side}</StatusPill>}
      </td>
      <td className={td}>
        {p.quantity} <span className="text-xs text-muted">({p.lots} lots)</span>
      </td>
      <td className={td}>
        <Priced price={p.entry_price} at={p.entry_time} />
      </td>
      <td className={td}>
        {p.status === "open" ? num(p.last_ltp) : <Priced price={p.exit_price} at={p.exit_time} />}
      </td>
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
  );
}

/** One Telegram tip: a summary row (the buy: total quantity, average price and time; the trade's P&L), then each
 * quarter with where and when it was booked. */
function TipTradeRows({ parts }: { parts: Position[] }) {
  const qty = parts.reduce((n, p) => n + p.quantity, 0);
  const lots = parts.reduce((n, p) => n + p.lots, 0);
  const avg = (xs: [number, number][]) =>
    xs.reduce((a, [px, q]) => a + px * q, 0) / xs.reduce((a, [, q]) => a + q, 0);
  const entry = avg(parts.map((p) => [p.entry_price, p.quantity]));
  const open = parts.filter((p) => p.status === "open");
  const closed = parts.filter((p) => p.status !== "open" && p.exit_price != null);
  const first = parts.map((p) => p.entry_time).sort()[0];
  const p0 = parts[0]!;
  return (
    <>
      <tr className="bg-surface-2/50 font-medium">
        <td className={td}>
          <p className="font-semibold">
            {p0.underlying} {p0.strike} {p0.option_type}
          </p>
          <p className="text-xs text-muted">
            {p0.expiry} · Telegram tip · {parts.length} parts
          </p>
        </td>
        <td className={td}>
          <StatusPill tone={p0.side === "BUY" ? "success" : "danger"}>{p0.side}</StatusPill>
        </td>
        <td className={td}>
          {qty} <span className="text-xs text-muted">({lots} lots)</span>
        </td>
        <td className={td}>
          <Priced price={Math.round(entry * 100) / 100} at={first} />
        </td>
        <td className={td}>
          {open.length
            ? num(open[0]!.last_ltp)
            : num(Math.round(avg(closed.map((p) => [p.exit_price!, p.quantity])) * 100) / 100)}
          {!open.length && <p className="text-xs text-muted">average exit</p>}
        </td>
        <td className={td}>{num(open[0]?.current_sl)}</td>
        <td className={td}>–</td>
        <td className={td}>
          <Pnl value={totalPnl(parts)} className="font-semibold" />
        </td>
        <td className={td}>
          {open.length ? (
            <StatusPill tone="success">
              {open.length} open · {parts.length - open.length} booked
            </StatusPill>
          ) : (
            <span className="text-xs text-muted">all booked</span>
          )}
        </td>
      </tr>
      {parts.map((p) => (
        <PositionRow key={p.id} p={p} sub />
      ))}
    </>
  );
}

/** The P&L of the positions shown, booked and open together. */
export function totalPnl(positions: readonly Position[]): number {
  return Math.round(positions.reduce((sum, p) => sum + (p.pnl ?? 0), 0) * 100) / 100;
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  const [from, setFrom] = useState(""); // "" = today
  const [to, setTo] = useState("");
  const [filter, setFilter] = useState<Filter>("current");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    // the choices survive a page refresh (kept in this browser only); a date range picked on an earlier day is not
    // carried into a new day: the page opens on today's trades again
    const t = setTimeout(() => {
      try {
        if (localStorage.getItem(RUNNING_ONLY_KEY) === "1") setFilter("open");
        const saved = JSON.parse(localStorage.getItem(RANGE_KEY) ?? "null") as {
          day: string;
          from: string;
          to: string;
        } | null;
        if (saved && saved.day === ist.format(new Date())) {
          setFrom(saved.from);
          setTo(saved.to);
        }
      } catch {
        /* storage unavailable: today's trades */
      }
      setLoaded(true);
    }, 0);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => {
    if (!loaded) return;
    try {
      localStorage.setItem(RANGE_KEY, JSON.stringify({ day: ist.format(new Date()), from, to }));
    } catch {
      /* not remembered: fine */
    }
  }, [from, to, loaded]);
  const choose = (runningOnly: boolean) => {
    setFilter(runningOnly ? "open" : "current");
    try {
      localStorage.setItem(RUNNING_ONLY_KEY, runningOnly ? "1" : "0");
    } catch {
      /* not remembered: fine */
    }
  };
  const runningOnly = filter === "open";
  const shown = runningOnly ? currentPositions(positions, "open") : positionsBetween(positions, from, to);
  const todayOnly = !from && !to;
  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <CardTitle>Positions</CardTitle>
          {shown.length > 0 && (
            <span className="text-xs text-muted">
              ({shown.length} position{shown.length === 1 ? "" : "s"}
              {runningOnly ? ", running" : todayOnly ? ", today's" : ""})
            </span>
          )}
        </div>
        {positions.length > 0 && (
          <div className="flex flex-wrap items-center gap-6 text-sm">
            {shown.length > 0 && (
              <span className="text-muted">
                Total P&amp;L{" "}
                <span className="text-lg font-semibold">
                  <Pnl value={totalPnl(shown)} />
                </span>
              </span>
            )}
            {!runningOnly && (
              <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <input
                  type="date"
                  aria-label="Trades from"
                  className="h-8 rounded border border-border bg-surface px-1.5 text-xs text-foreground"
                  value={from || ist.format(new Date())}
                  onChange={(e) => setFrom(e.target.value)}
                />
                to
                <input
                  type="date"
                  aria-label="Trades to"
                  className="h-8 rounded border border-border bg-surface px-1.5 text-xs text-foreground"
                  value={to || ist.format(new Date())}
                  onChange={(e) => setTo(e.target.value)}
                />
                {!todayOnly && (
                  <button
                    type="button"
                    className="text-primary-text underline"
                    onClick={() => {
                      setFrom("");
                      setTo("");
                    }}
                  >
                    Today
                  </button>
                )}
              </span>
            )}
            <label className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" checked={filter === "open"} onChange={(e) => choose(e.target.checked)} />
              Running trades only ({currentPositions(positions, "open").length})
            </label>
          </div>
        )}
      </div>
      {positions.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">
          No positions yet. The strategy enters at its entry time.
        </p>
      ) : shown.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">
          {runningOnly
            ? "No trade is running."
            : todayOnly
              ? "Nothing open or traded today."
              : "No trades in these days."}
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
              {groupTipTrades(shown).map((g) =>
                g.length === 1 ? (
                  <PositionRow key={g[0]!.id} p={g[0]!} />
                ) : (
                  <TipTradeRows key={g[0]!.id} parts={g} />
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
