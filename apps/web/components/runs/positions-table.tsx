"use client";

import type { RunDetail } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import { useState } from "react";

type Position = RunDetail["positions"][number];

const th = "px-3 py-2 font-medium whitespace-nowrap";
const td = "px-3 py-2.5 whitespace-nowrap";
const num = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v));
const ist = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }); // yyyy-mm-dd
const dayOf = (t: string | null | undefined) => (t ? ist.format(new Date(t)) : null);

type Filter = "current" | "open" | "all";

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

/** The P&L of the positions shown, booked and open together. */
export function totalPnl(positions: readonly Position[]): number {
  return Math.round(positions.reduce((sum, p) => sum + (p.pnl ?? 0), 0) * 100) / 100;
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  const [filter, setFilter] = useState<Filter>("current");
  const shown = currentPositions(positions, filter);
  const hidden = positions.length - currentPositions(positions, "current").length;
  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-2">
        <CardTitle>Positions</CardTitle>
        {positions.length > 0 && (
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={filter === "open"}
                onChange={(e) => setFilter(e.target.checked ? "open" : "current")}
              />
              Running trades only ({currentPositions(positions, "open").length})
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-muted">
              <input
                type="checkbox"
                checked={filter === "all"}
                onChange={(e) => setFilter(e.target.checked ? "all" : "current")}
              />
              Earlier days too ({positions.length})
            </label>
          </div>
        )}
      </div>
      {positions.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">
          No positions yet. The strategy enters at its entry time.
        </p>
      ) : shown.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">Nothing open or traded today.</p>
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
              {shown.map((p) => (
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
            <tfoot>
              <tr className="border-t-2 border-border font-semibold">
                <td className={td} colSpan={7}>
                  Total P&amp;L{" "}
                  <span className="text-xs font-normal text-muted">
                    ({shown.length} position{shown.length === 1 ? "" : "s"}
                    {filter === "current" ? ": open and today's" : filter === "open" ? ": open" : ""})
                  </span>
                </td>
                <td className={td}>
                  <Pnl value={totalPnl(shown)} />
                </td>
                <td className={td} />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {filter === "current" && hidden > 0 && (
        <p className="px-5 pb-4 text-xs text-muted">
          {hidden} closed position{hidden === 1 ? "" : "s"} from earlier days hidden: tick &quot;Earlier days
          too&quot; to see them.
        </p>
      )}
    </Card>
  );
}
