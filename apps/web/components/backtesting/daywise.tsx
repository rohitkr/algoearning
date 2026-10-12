import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl } from "@algoearning/ui";
import { useState } from "react";

import type { Day } from "./result-view";

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];
const monthYear = new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", month: "short", year: "numeric" });
const dayTitle = new Intl.DateTimeFormat("en-IN", {
  timeZone: "UTC",
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
});

const at = (iso: string) => new Date(`${iso}T00:00:00Z`);
const iso = (d: Date) => d.toISOString().slice(0, 10);
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${formatNumber(Math.abs(v))}`;
const label = (d: Day) =>
  `${dayTitle.format(at(d.day)).replace(/, (\d{4})$/, " $1")}: ${signed(d.pnl)} (${d.trades} trade${d.trades === 1 ? "" : "s"})`;

export type MonthBlock = {
  key: string; // 2026-04
  title: string; // Apr 2026
  total: number;
  traded: number; // days with a trade
  lead: number; // empty cells before the 1st (Sunday first)
  days: { date: string; day: number; entry: Day | null }[];
};

/** Every calendar month the result touches: its days (with the day's result if it traded) and the month's net P&L. */
export function monthBlocks(daily: readonly Day[]): MonthBlock[] {
  if (!daily.length) return [];
  const byDay = new Map(daily.map((d) => [d.day, d]));
  const first = at(daily.reduce((a, d) => (d.day < a ? d.day : a), daily[0]!.day));
  const last = at(daily.reduce((a, d) => (d.day > a ? d.day : a), daily[0]!.day));
  const out: MonthBlock[] = [];
  for (
    let m = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1));
    m <= last;
    m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))
  ) {
    const count = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0)).getUTCDate();
    const days = Array.from({ length: count }, (_, i) => {
      const date = iso(new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), i + 1)));
      return { date, day: i + 1, entry: byDay.get(date) ?? null };
    });
    const traded = days.filter((d) => d.entry);
    out.push({
      key: iso(m).slice(0, 7),
      title: monthYear.format(m),
      total: Math.round(traded.reduce((a, d) => a + (d.entry?.pnl ?? 0), 0) * 100) / 100,
      traded: traded.length,
      lead: m.getUTCDay(),
      days,
    });
  }
  return out;
}

const money = (v: number) =>
  `${v < 0 ? "-" : "+"}₹${Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const tipDay = new Intl.DateTimeFormat("en-IN", {
  timeZone: "UTC",
  day: "numeric",
  month: "short",
  year: "numeric",
});

/** A day's square: solid green for a profit, solid red for a loss, quiet grey when nothing traded. */
const tone = (e: Day | null) =>
  e == null ? "bg-surface-2" : e.pnl > 0 ? "bg-profit" : e.pnl < 0 ? "bg-loss" : "bg-muted";

/** One calendar per month with the month's net result, like a broker's day-wise breakdown. */
export function MonthCards({ daily }: { daily: readonly Day[] }) {
  const months = monthBlocks(daily);
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {months.map((m) => (
        <MonthCard key={m.key} m={m} />
      ))}
    </div>
  );
}

function MonthCard({ m }: { m: MonthBlock }) {
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null);
  const show = (el: HTMLElement, d: Day) =>
    setTip({
      text: `${tipDay.format(at(d.day))} • ${money(d.pnl)}`,
      x: el.offsetLeft + el.offsetWidth / 2,
      y: el.offsetTop,
    });
  return (
    <section
      aria-label={m.title}
      className="flex w-[17.5rem] shrink-0 flex-col gap-3 rounded-xl border border-border p-4"
    >
      <div className="relative grid grid-cols-7 gap-1.5 text-center text-xs text-muted">
        {WEEKDAYS.map((w, i) => (
          <span key={i}>{w}</span>
        ))}
        {Array.from({ length: m.lead }, (_, i) => (
          <span key={`lead-${i}`} />
        ))}
        {m.days.map((d) =>
          d.entry ? (
            <button
              type="button"
              key={d.date}
              aria-label={label(d.entry)}
              onMouseEnter={(e) => show(e.currentTarget, d.entry!)}
              onFocus={(e) => show(e.currentTarget, d.entry!)}
              onMouseLeave={() => setTip(null)}
              onBlur={() => setTip(null)}
              className={`mx-auto size-6 rounded-md outline-offset-2 ${tone(d.entry)}`}
            />
          ) : (
            <span key={d.date} className={`mx-auto size-6 rounded-md ${tone(null)}`} />
          ),
        )}
        {tip && (
          <div
            role="status"
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[calc(100%+6px)] rounded-lg bg-black px-3 py-1.5 text-xs font-medium whitespace-nowrap text-white shadow-lg"
            style={{ left: tip.x, top: tip.y }}
          >
            {tip.text}
          </div>
        )}
      </div>
      <div className="text-center">
        <p className="text-sm">{m.title}</p>
        <p className="text-lg font-semibold tabular-nums">
          <Pnl value={m.total} />
        </p>
      </div>
    </section>
  );
}

/** The result's day-wise picture: a calendar per month with each day's result and the month's total. */
export function DaywiseBreakdown({ daily }: { daily: readonly Day[] }) {
  if (!daily.length) return null;
  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Daywise breakdown</CardTitle>
      <MonthCards daily={daily} />
    </Card>
  );
}
