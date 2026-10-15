"use client";

import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl, StatusPill } from "@algoearning/ui";
import { ChevronDown, Download, FileText } from "lucide-react";
import { useEffect, useState } from "react";

import { type ReportMeta, downloadCsv, downloadPdf } from "@/lib/backtest-export";

import type { BacktestResultData, Trade } from "./result-view";

const IST = "Asia/Kolkata";
const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: IST }); // 2026-09-15
const dayTitle = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
});
const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const shortDay = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  weekday: "short",
  day: "numeric",
  month: "short",
});

export type DayGroup = { key: string; title: string; net: number; trades: Trade[] };

/** The trades grouped by the day they were entered (Indian time), oldest first, with each day's combined net P&L: all
 * the legs of one trade stay together, also when held overnight (the calendar and summary count the same way). */
export function groupByDay(trades: readonly Trade[]): DayGroup[] {
  const groups = new Map<string, DayGroup>();
  for (const t of trades) {
    const when = new Date(t.entry_time);
    const key = dayKey.format(when);
    const g = groups.get(key) ?? {
      key,
      title: dayTitle.format(when).replace(/, (\d{4})$/, " $1"),
      net: 0,
      trades: [],
    };
    g.trades.push(t);
    g.net = Math.round((g.net + t.net) * 100) / 100;
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/** BUY green, SELL red: the same pills as the run page. */
function SideBadge({ side }: { side: string }) {
  return <StatusPill tone={side === "BUY" ? "success" : "danger"}>{side}</StatusPill>;
}

function When({ iso, withDay }: { iso: string; withDay: boolean }) {
  const d = new Date(iso);
  return (
    <>
      Time: {clock.format(d)}
      {withDay && (
        <>
          <br />
          <span className="text-muted">Date: {shortDay.format(d)}</span>
        </>
      )}
    </>
  );
}

/** Trades one row per day with the day's combined P&L; a click opens the day's individual trades. */
export function TradesByDay({
  trades,
  total,
  report,
}: {
  trades: readonly Trade[];
  total: number;
  /** With it, the card offers the whole result as a CSV and a PDF report. */
  report?: { res: BacktestResultData; meta: ReportMeta };
}) {
  const [busy, setBusy] = useState(false);
  const groups = groupByDay(trades);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set(groups.slice(0, 1).map((g) => g.key)));
  // a day clicked in the calendar: open it and scroll to it
  useEffect(() => {
    const go = (e: Event) => {
      const key = (e as CustomEvent<string>).detail;
      setOpen((o) => new Set(o).add(key));
      requestAnimationFrame(() =>
        document.getElementById(`day-row-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    };
    window.addEventListener("backtest-day", go);
    return () => window.removeEventListener("backtest-day", go);
  }, []);
  const toggle = (key: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const allOpen = groups.length > 0 && open.size === groups.length;

  return (
    <Card className="p-0">
      <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-2">
        <CardTitle>
          Trades by day {total > trades.length && `(first ${trades.length} of ${total} trades)`}
        </CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted hover:text-foreground"
            onClick={() => setOpen(allOpen ? new Set() : new Set(groups.map((g) => g.key)))}
          >
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
          {report && (
            <>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted hover:text-foreground"
                onClick={() => downloadCsv(report.res, report.meta)}
              >
                Download CSV <Download className="size-3.5" aria-hidden />
              </button>
              <button
                type="button"
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted hover:text-foreground disabled:opacity-60"
                onClick={async () => {
                  setBusy(true);
                  try {
                    await downloadPdf(report.res, report.meta);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "Preparing…" : "Download PDF"} <FileText className="size-3.5" aria-hidden />
              </button>
            </>
          )}
        </div>
      </div>
      <div className="flex flex-col gap-3 px-3 pb-4 sm:px-5">
        {groups.map((g) => {
          const isOpen = open.has(g.key);
          const panel = `day-${g.key}`;
          return (
            <section
              key={g.key}
              id={`day-row-${g.key}`}
              className="scroll-mt-20 overflow-hidden rounded-xl border border-border"
            >
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={panel}
                onClick={() => toggle(g.key)}
                className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
              >
                <span className="flex flex-col sm:flex-row sm:items-baseline sm:gap-3">
                  <span className="font-medium">{g.title}</span>
                  <span className="text-xs text-muted">
                    {g.trades.length} trade{g.trades.length === 1 ? "" : "s"} ·{" "}
                    {g.trades.filter((t) => t.side === "SELL").length} sell ·{" "}
                    {g.trades.filter((t) => t.side === "BUY").length} buy
                  </span>
                </span>
                <span className="flex items-center gap-3">
                  <span className="text-xs text-muted">Profit/Loss</span>
                  <span
                    className={`rounded-full px-3 py-1 text-sm font-semibold tabular-nums ${
                      g.net >= 0 ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
                    }`}
                  >
                    {formatNumber(g.net)}
                  </span>
                  <ChevronDown
                    className={`size-4 transition-transform ${isOpen ? "rotate-180" : ""}`}
                    aria-hidden
                  />
                </span>
              </button>
              {isOpen && (
                <div id={panel} className="overflow-x-auto border-t border-border">
                  <table className="w-full min-w-[48rem] text-left text-sm tabular-nums">
                    <thead>
                      <tr className="text-xs text-muted">
                        {["Symbol", "Side", "Qty", "Entry", "Exit", "Net P&L", "Charges", "Exit reason"].map(
                          (h) => (
                            <th key={h} className="px-4 py-2 font-medium whitespace-nowrap">
                              {h}
                            </th>
                          ),
                        )}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {g.trades.map((t, i) => {
                        return (
                          <tr key={i}>
                            <td className="px-4 py-2 whitespace-nowrap">{t.contract}</td>
                            <td className="px-4 py-2">
                              <SideBadge side={t.side} />
                            </td>
                            <td className="px-4 py-2">
                              {t.side === "SELL" ? "−" : "+"}
                              {t.qty}
                            </td>
                            <td className="px-4 py-2 whitespace-nowrap">
                              Price: {formatNumber(t.entry_price)}
                              <br />
                              <When iso={t.entry_time} withDay />
                            </td>
                            <td className="px-4 py-2 whitespace-nowrap">
                              Price: {formatNumber(t.exit_price)}
                              <br />
                              <When iso={t.exit_time} withDay />
                            </td>
                            <td className="px-4 py-2">
                              <Pnl value={t.net} />
                            </td>
                            <td className="px-4 py-2 text-muted">{formatNumber(t.charges)}</td>
                            <td className="px-4 py-2 text-xs text-muted">{t.reason}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          );
        })}
      </div>
    </Card>
  );
}
