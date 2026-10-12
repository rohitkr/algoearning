import { formatNumber } from "@algoearning/shared";
import { Card, CardTitle, Pnl } from "@algoearning/ui";

import type { Day } from "./result-view";

const MS_DAY = 86_400_000;
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];
const monthName = new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", month: "short" });
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

/** Colour for a day: green for profit, red for loss, deeper the bigger it is next to the biggest day. */
function shade(pnl: number, max: number): React.CSSProperties {
  const strength = Math.round(30 + 70 * Math.min(1, Math.abs(pnl) / Math.max(1, max)));
  const tone = pnl >= 0 ? "var(--profit)" : "var(--loss)";
  return { backgroundColor: `color-mix(in srgb, ${tone} ${strength}%, transparent)` };
}

/** GitHub-style year view: a column per week (Sunday to Saturday), a row per weekday, a square per day. */
export function PnlHeatmap({ daily }: { daily: readonly Day[] }) {
  if (!daily.length) return null;
  const byDay = new Map(daily.map((d) => [d.day, d]));
  const max = Math.max(...daily.map((d) => Math.abs(d.pnl)));
  const days = daily.map((d) => d.day).sort();
  const start = new Date(at(days[0]!).getTime() - at(days[0]!).getUTCDay() * MS_DAY);
  const end = at(days[days.length - 1]!);
  const weeks: Date[] = [];
  for (let w = start; w <= end; w = new Date(w.getTime() + 7 * MS_DAY)) weeks.push(w);
  return (
    <div className="overflow-x-auto pb-1">
      <div className="inline-flex flex-col gap-1">
        <div className="flex gap-1 text-[10px] text-muted">
          {weeks.map((w, i) => {
            const firstOfMonth = Array.from({ length: 7 }, (_, k) => new Date(w.getTime() + k * MS_DAY)).find(
              (d) => d.getUTCDate() === 1,
            );
            const showFirst = i === 0 && !firstOfMonth;
            const name = firstOfMonth ? monthName.format(firstOfMonth) : showFirst ? monthName.format(w) : "";
            return (
              <span key={w.toISOString()} className="w-3.5 overflow-visible whitespace-nowrap uppercase">
                {name}
              </span>
            );
          })}
        </div>
        <div className="flex gap-1" role="img" aria-label="Profit and loss by day, one square per day">
          {weeks.map((w) => (
            <div key={w.toISOString()} className="flex flex-col gap-1">
              {Array.from({ length: 7 }, (_, k) => {
                const date = iso(new Date(w.getTime() + k * MS_DAY));
                const d = byDay.get(date);
                return (
                  <span
                    key={date}
                    title={d ? label(d) : undefined}
                    className="size-3.5 rounded-[3px] bg-surface-2"
                    style={d ? shade(d.pnl, max) : undefined}
                  />
                );
              })}
            </div>
          ))}
        </div>
        <div className="mt-1 flex items-center justify-end gap-1.5 text-[10px] text-muted">
          Loss
          {[-1, -0.5, 0, 0.5, 1].map((f) => (
            <span
              key={f}
              className="size-3 rounded-[3px] bg-surface-2"
              style={f === 0 ? undefined : shade(f * max, max)}
            />
          ))}
          Profit
        </div>
      </div>
    </div>
  );
}

/** One calendar per month with the month's net result, like a broker's day-wise breakdown. */
export function MonthCards({ daily }: { daily: readonly Day[] }) {
  const months = monthBlocks(daily);
  const max = Math.max(1, ...daily.map((d) => Math.abs(d.pnl)));
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {months.map((m) => (
        <section
          key={m.key}
          aria-label={m.title}
          className="flex w-[17.5rem] shrink-0 flex-col gap-3 rounded-xl border border-border p-4"
        >
          <div className="grid grid-cols-7 gap-1.5 text-center text-xs text-muted">
            {WEEKDAYS.map((w, i) => (
              <span key={i}>{w}</span>
            ))}
            {Array.from({ length: m.lead }, (_, i) => (
              <span key={`lead-${i}`} />
            ))}
            {m.days.map((d) => (
              <span
                key={d.date}
                title={d.entry ? label(d.entry) : undefined}
                aria-label={d.entry ? label(d.entry) : undefined}
                className="mx-auto size-6 rounded-md bg-surface-2"
                style={d.entry ? shade(d.entry.pnl, max) : undefined}
              />
            ))}
          </div>
          <div className="text-center">
            <p className="text-sm font-medium">{m.title}</p>
            <p className="text-base font-semibold tabular-nums">
              <Pnl value={m.total} />
            </p>
            <p className="text-xs text-muted">
              {m.traded} trading day{m.traded === 1 ? "" : "s"}
            </p>
          </div>
        </section>
      ))}
    </div>
  );
}

/** The result's day-wise picture: the whole period at a glance, then every month with its total. */
export function DaywiseBreakdown({ daily }: { daily: readonly Day[] }) {
  if (!daily.length) return null;
  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>Daywise breakdown</CardTitle>
      <PnlHeatmap daily={daily} />
      <MonthCards daily={daily} />
    </Card>
  );
}
