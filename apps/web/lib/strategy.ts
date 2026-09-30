// Plain-language view of a strategy config (ae_core.strategy on the API), shared by the list and the builder.
import type {
  ConfigIssue,
  Instrument,
  LegStrike,
  LegThreshold,
  StrategyConfig,
  StrategyLeg,
  TimeBasedConfig,
} from "@algoearning/api-types";

export const KIND_LABEL: Record<StrategyConfig["kind"], string> = {
  time_based: "Time based",
  range_breakout: "Range breakout",
  zero_dte: "Expiry-day straddle",
};

export const EXPIRY_LABEL: Record<StrategyLeg["expiry"], string> = {
  current_week: "Current week",
  next_week: "Next week",
  current_month: "Current month",
  next_month: "Next month",
};

export const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI"] as const;
export type Weekday = (typeof WEEKDAYS)[number];
export type Underlying = StrategyConfig["underlying"];

type Timing = NonNullable<TimeBasedConfig["timing"]>;
type Risk = NonNullable<TimeBasedConfig["risk"]>;

/** A time-based config's timing and risk with the API's defaults filled in, ready to spread and change. */
export function timingOf(c: TimeBasedConfig): Timing {
  return { entry: "09:20", exit: "15:15", days: [...WEEKDAYS], ...c.timing };
}

export function riskOf(c: TimeBasedConfig): Risk {
  return { mtm_stop_loss: null, mtm_target: null, exit_all_on_leg_sl: false, ...c.risk };
}

/** "ATM", "OTM 2", "ITM 1" (offset > 0 is out of the money), or "Premium ≈ ₹50". */
export function strikeLabel(s: LegStrike | undefined): string {
  if (!s || s.mode === "atm") {
    const n = s?.offset ?? 0;
    return n === 0 ? "ATM" : n > 0 ? `OTM ${n}` : `ITM ${-n}`;
  }
  return `Premium ≈ ₹${s.premium ?? "?"}`;
}

export function thresholdLabel(t: LegThreshold): string {
  const unit = t.unit === "points" ? " pts" : "%";
  return `${t.value}${unit}${t.basis === "underlying" ? " on index" : ""}`;
}

export function legQuantity(leg: StrategyLeg, inst: Instrument | undefined): number | null {
  return inst ? leg.lots * inst.lot_size : null;
}

/** "Sell 1 lot (65 qty) NIFTY OTM 2 CE · Current week · SL 30%" */
export function describeLeg(leg: StrategyLeg, underlying: string, inst: Instrument | undefined): string {
  const qty = legQuantity(leg, inst);
  const lots = `${leg.lots} lot${leg.lots === 1 ? "" : "s"}${qty ? ` (${qty} qty)` : ""}`;
  const parts = [
    `${leg.action === "BUY" ? "Buy" : "Sell"} ${lots} ${underlying} ${strikeLabel(leg.strike)} ${leg.option_type}`,
    EXPIRY_LABEL[leg.expiry],
  ];
  if (leg.stop_loss) parts.push(`SL ${thresholdLabel(leg.stop_loss)}`);
  if (leg.target) parts.push(`Target ${thresholdLabel(leg.target)}`);
  if (leg.trailing) {
    const u = leg.trailing.unit === "points" ? " pts" : "%";
    parts.push(`Trail ${leg.trailing.step}${u} every ${leg.trailing.trigger}${u}`);
  }
  if (leg.reentry_on_sl) parts.push(`Re-enter after SL ×${leg.reentry_on_sl.count}`);
  if (leg.reentry_on_target) parts.push(`Re-enter after target ×${leg.reentry_on_target.count}`);
  return parts.join(" · ");
}

function days(d: readonly string[] | undefined): string {
  if (!d || d.length === 5) return "every weekday";
  return d.map((x) => x.slice(0, 1) + x.slice(1).toLowerCase()).join(", ");
}

/** The whole strategy in a few sentences (builder summary panel). */
export function describeConfig(c: StrategyConfig, instruments: Instrument[]): string[] {
  const inst = instruments.find((i) => i.code === c.underlying);
  if (c.kind === "time_based") {
    const t = c.timing;
    const lines = [
      `Enter at ${t?.entry ?? "09:20"} and exit at ${t?.exit ?? "15:15"}, ${days(t?.days)}.`,
      ...c.legs.map((l, i) => `Leg ${i + 1}: ${describeLeg(l, c.underlying, inst)}`),
    ];
    const r = c.risk;
    if (r?.mtm_stop_loss) lines.push(`Exit everything if the loss reaches ₹${r.mtm_stop_loss}.`);
    if (r?.mtm_target) lines.push(`Exit everything if the profit reaches ₹${r.mtm_target}.`);
    if (r?.exit_all_on_leg_sl) lines.push("When any leg's stop-loss hits, exit every leg.");
    return lines;
  }
  const lots = `${c.lots} lot${c.lots === 1 ? "" : "s"}${inst ? ` (${c.lots * inst.lot_size} qty)` : ""}`;
  const hedge = c.hedge_width ? `, hedged ${c.hedge_width} points further out` : ", unhedged";
  if (c.kind === "range_breakout")
    return [
      `Mark the ${c.underlying} high and low from ${c.range_start} to ${c.range_end}.`,
      `On the first close outside it (until ${c.last_entry}), sell ${lots} of an option ${c.itm_points} points in the money: a put on an upside breakout, a call on a downside one${hedge}.`,
      `Stop-loss when ${c.underlying} moves ${c.stop_loss_pct}% against the entry${c.reentry ? ", with one re-entry at cost" : ""}.`,
      c.intraday_only
        ? `Exit at ${c.exit_time} the same day.`
        : `Hold to the weekly expiry${c.expiry_offset ? ` +${c.expiry_offset}` : ""} and exit at ${c.exit_time}.`,
    ];
  return [
    `On each ${c.underlying} expiry day, sell ${lots} each of a call and a put ${c.itm_points} points in the money${hedge}.`,
    `Entry time: the best of every ${c.step_minutes} minutes from ${c.first_entry} to ${c.last_entry}, judged on the last ${c.lookback} expiry days.`,
    `Stop-loss ${c.stop_loss_pct}% on each leg's premium${c.reentry ? ", with one re-entry" : ""}; exit at ${c.exit_time}.`,
  ];
}

/** One line for the strategies list: "NIFTY · 2 legs · 09:20–15:15". */
export function configSummary(c: StrategyConfig): string {
  if (c.kind === "time_based") {
    const n = c.legs.length;
    return `${c.underlying} · ${n} leg${n === 1 ? "" : "s"} · ${c.timing?.entry ?? "09:20"}–${c.timing?.exit ?? "15:15"}`;
  }
  if (c.kind === "range_breakout") return `${c.underlying} · range ${c.range_start}–${c.range_end}`;
  return `${c.underlying} · expiry days · ${c.first_entry}–${c.last_entry}`;
}

/** Next free leg id: L1, L2, ... */
export function nextLegId(legs: StrategyLeg[]): string {
  const used = new Set(legs.map((l) => l.id));
  let n = legs.length + 1;
  while (used.has(`L${n}`)) n += 1;
  return `L${n}`;
}

export function newLeg(c: TimeBasedConfig, weekly: boolean): StrategyLeg {
  return {
    id: nextLegId(c.legs),
    action: "SELL",
    option_type: "CE",
    lots: 1,
    expiry: weekly ? "current_week" : "current_month",
    strike: { mode: "atm", offset: 0, premium: null },
    stop_loss: null,
    target: null,
    trailing: null,
    reentry_on_sl: null,
    reentry_on_target: null,
  };
}

/** Issue path as a lookup key: ["legs", 0, "lots"] -> "legs.0.lots". Save errors carry ["body", "config", ...] and
 * sometimes the union tag first; both are dropped so every source of errors uses the same keys. */
export function issueKey(loc: ConfigIssue["loc"]): string {
  let l = [...loc];
  if (l[0] === "body" && l[1] === "config") l = l.slice(2);
  if (typeof l[0] === "string" && l[0] in KIND_LABEL) l = l.slice(1);
  return l.join(".");
}

export function issueMap(issues: ConfigIssue[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const i of issues ?? []) out[issueKey(i.loc)] ??= i.msg;
  return out;
}

/** Human label for an issue path, e.g. "Leg 2 · stop loss · value". */
export function issueLabel(key: string): string {
  if (!key) return "Strategy";
  return key
    .split(".")
    .map((p, i, all) => (all[i - 1] === "legs" && /^\d+$/.test(p) ? `Leg ${Number(p) + 1}` : p))
    .filter((p) => p !== "legs")
    .map((p) => p.replaceAll("_", " "))
    .join(" · ");
}
