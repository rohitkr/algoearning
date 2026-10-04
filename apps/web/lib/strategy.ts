// Plain-language view of a strategy config (ae_core.strategy on the API), shared by the list and the builder.
import type {
  ConfigIssue,
  Instrument,
  LegStrike,
  LegThreshold,
  RulesCondition,
  RulesConditionGroup,
  RulesConfig,
  RulesEntry,
  RulesHolding,
  RulesOperand,
  RulesRisk,
  SmcScalpConfig,
  StrategyConfig,
  StrategyLeg,
  TimeBasedConfig,
} from "@algoearning/api-types";

export const KIND_LABEL: Record<StrategyConfig["kind"], string> = {
  rules: "Rule builder",
  time_based: "Time based",
  range_breakout: "Range breakout",
  zero_dte: "Expiry-day straddle",
  smc_scalp: "SMC options scalping",
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

export const HOLD_LABEL: Record<RulesHolding["mode"], string> = {
  intraday: "Same day (intraday)",
  next_day: "Next trading day",
  days: "Some trading days",
  expiry: "Until expiry day",
};

export const TIMEFRAMES = [1, 3, 5, 10, 15, 30, 60] as const;

export const OP_LABEL: Record<RulesCondition["op"], string> = {
  crosses_above: "crosses above",
  crosses_below: "crosses below",
  above: "is above",
  below: "is below",
};

export const LEVEL_LABEL: Record<Extract<RulesOperand, { kind: "level" }>["name"], string> = {
  opening_range_high: "Opening range high",
  opening_range_low: "Opening range low",
  day_open: "Today's open",
  day_high: "Today's high so far",
  day_low: "Today's low so far",
  prev_high: "Previous day's high",
  prev_low: "Previous day's low",
  prev_close: "Previous day's close",
  price_at: "Price at a time",
};

/** A starting condition: the 5-minute close crossing above the first 15 minutes' high. */
export function newCondition(): RulesCondition {
  return {
    timeframe: 5,
    left: { kind: "price", field: "close" },
    op: "crosses_above",
    right: { kind: "level", name: "opening_range_high", minutes: 15, at: null, offset: 0 },
  };
}

export function newGroup(): RulesConditionGroup {
  return { match: "all", conditions: [newCondition()] };
}

/** "the 5-minute close", "the first 15 minutes' high + 10", "50". */
export function operandText(o: RulesOperand, tf: number): string {
  if (o.kind === "number") return String(o.value);
  if (o.kind === "price") return `the ${tf}-minute ${o.field ?? "close"}`;
  const off = o.offset ? ` ${o.offset > 0 ? "+" : "−"} ${Math.abs(o.offset)}` : "";
  const name =
    o.name === "opening_range_high"
      ? `the first ${o.minutes ?? 15} minutes' high`
      : o.name === "opening_range_low"
        ? `the first ${o.minutes ?? 15} minutes' low`
        : o.name === "price_at"
          ? `the price at ${o.at ?? "?"}`
          : LEVEL_LABEL[o.name].replace(/^(.)/, (x) => x.toLowerCase());
  return name + off;
}

export function groupText(g: RulesConditionGroup | null | undefined): string {
  const parts = (g?.conditions ?? []).map((c) => {
    const tf = c.timeframe ?? 5;
    return `${operandText(c.left ?? { kind: "price", field: "close" }, tf)} ${OP_LABEL[c.op ?? "crosses_above"]} ${operandText(c.right, tf)}`;
  });
  return parts.join(g?.match === "any" ? " or " : " and ");
}

/** A rules config's parts with the API's defaults filled in, ready to spread and change. */
export function entryOf(c: RulesConfig): RulesEntry {
  return {
    mode: "time",
    at: "09:20",
    until: null,
    days: [...WEEKDAYS],
    dte: null,
    when: null,
    when_mirrored: null,
    max_entries: 1,
    ...c.entry,
  };
}

export function holdingOf(c: RulesConfig): RulesHolding {
  return {
    mode: "intraday",
    exit: "15:15",
    days: 1,
    exit_when: null,
    exit_when_mirrored: null,
    ...c.holding,
  };
}

export function rulesRiskOf(c: RulesConfig): RulesRisk {
  return {
    mtm_stop_loss: null,
    mtm_target: null,
    exit_all_on_leg_sl: false,
    combined_stop: null,
    lock_profit: null,
    ...c.risk,
  };
}

/** A builder config from before ADR 0022 as the intraday rules it always meant (the API stores it so too). */
export function rulesFromTimeBased(c: TimeBasedConfig): RulesConfig {
  const t = timingOf(c);
  const r = riskOf(c);
  return {
    kind: "rules",
    underlying: c.underlying,
    entry: { mode: "time", at: t.entry, until: null, days: t.days, dte: null, max_entries: 1 },
    holding: { mode: "intraday", exit: t.exit, days: 1 },
    legs: c.legs,
    risk: { ...r, combined_stop: null, lock_profit: null },
  };
}

/** A time-based config's timing and risk with the API's defaults filled in, ready to spread and change. */
export function timingOf(c: TimeBasedConfig): Timing {
  return { entry: "09:20", exit: "15:15", days: [...WEEKDAYS], ...c.timing };
}

export function riskOf(c: TimeBasedConfig): Risk {
  return { mtm_stop_loss: null, mtm_target: null, exit_all_on_leg_sl: false, ...c.risk };
}

/** "ATM", "OTM 2", "ITM 1" (offset > 0 is out of the money), "Premium ≈ ₹50", "Premium ≥ ₹50" or "300 pts OTM". */
export function strikeLabel(s: LegStrike | undefined): string {
  if (!s || s.mode === "atm") {
    const n = s?.offset ?? 0;
    return n === 0 ? "ATM" : n > 0 ? `OTM ${n}` : `ITM ${-n}`;
  }
  if (s.mode === "points") {
    const n = s.points ?? 0;
    return n === 0 ? "ATM" : `${Math.abs(n)} pts ${n > 0 ? "OTM" : "ITM"}`;
  }
  const sign = s.mode === "premium_gte" ? "≥" : s.mode === "premium_lte" ? "≤" : "≈";
  return `Premium ${sign} ₹${s.premium ?? "?"}`;
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
  if (c.kind === "rules") return describeRules(c, inst);
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
  if (c.kind === "smc_scalp") return describeSmc(c, inst);
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

function dteText(dte: number[]): string {
  return [...dte]
    .sort((x, y) => x - y)
    .map((n) => (n === 0 ? "on expiry day" : `${n} trading day${n === 1 ? "" : "s"} before expiry`))
    .join(" or ");
}

/** "exit at 09:30 the next trading day" */
export function holdingText(h: RulesHolding): string {
  if (h.mode === "intraday") return `exit at ${h.exit} the same day`;
  if (h.mode === "next_day") return `hold overnight and exit at ${h.exit} the next trading day`;
  if (h.mode === "days")
    return `hold and exit at ${h.exit}, ${h.days} trading day${h.days === 1 ? "" : "s"} later`;
  return `hold and exit at ${h.exit} on expiry day`;
}

function describeRules(c: RulesConfig, inst: Instrument | undefined): string[] {
  const e = entryOf(c);
  const h = holdingOf(c);
  const r = rulesRiskOf(c);
  const when = e.dte?.length ? `, only ${dteText(e.dte)}` : "";
  const until = e.until ? ` (not after ${e.until})` : "";
  const lines =
    e.mode === "signal"
      ? [
          `From ${e.at}${until}, ${days(e.days)}${when}, at most ${e.max_entries ?? 1} trade${(e.max_entries ?? 1) === 1 ? "" : "s"} a day; ${holdingText(h)}.`,
          ...(e.when ? [`Enter the legs below when ${groupText(e.when)}.`] : []),
          ...(e.when_mirrored
            ? [`Enter them mirrored (calls and puts swapped) when ${groupText(e.when_mirrored)}.`]
            : []),
        ]
      : [`Enter at ${e.at}${until}, ${days(e.days)}${when}; ${holdingText(h)}.`];
  lines.push(...c.legs.map((l, i) => `Leg ${i + 1}: ${describeLeg(l, c.underlying, inst)}`));
  if (h.exit_when) lines.push(`Exit early when ${groupText(h.exit_when)}.`);
  if (h.exit_when_mirrored)
    lines.push(`Exit a mirrored trade early when ${groupText(h.exit_when_mirrored)}.`);
  if (r.mtm_stop_loss) lines.push(`Exit everything if the trade's loss reaches ₹${r.mtm_stop_loss}.`);
  if (r.mtm_target) lines.push(`Exit everything if the trade's profit reaches ₹${r.mtm_target}.`);
  if (r.exit_all_on_leg_sl) lines.push("When any leg's stop-loss hits, exit every leg.");
  const cs = r.combined_stop;
  if (cs)
    lines.push(
      `Exit everything if the sold premiums together rise ${cs.value}${cs.unit === "percent" ? "%" : " points"} above their total at entry.`,
    );
  const lp = r.lock_profit;
  if (lp) {
    const trail =
      lp.trail_every && lp.trail_by ? `, raised by ₹${lp.trail_by} for every ₹${lp.trail_every} more` : "";
    lines.push(`Once the profit reaches ₹${lp.at}, keep at least ₹${lp.lock}${trail}.`);
  }
  return lines;
}

function describeSmc(c: SmcScalpConfig, inst: Instrument | undefined): string[] {
  const tf = { bias: 15, setup: 5, entry: 1, ...c.timeframes };
  const r = { lots: 1, rr: 2, tranches: true, max_trades_per_day: 2, max_losses_per_day: 2, ...c.risk };
  const s = { start: "09:30", last_entry: "14:30", exit: "15:10", ...c.session };
  const o = c.option;
  const lots = `${r.lots} lot${r.lots === 1 ? "" : "s"}${inst ? ` (${r.lots * inst.lot_size} qty)` : ""}`;
  return [
    `Bias from ${c.underlying}'s ${tf.bias}m structure; no trade while it is unclear.`,
    `Setup on ${tf.setup}m: a liquidity sweep, displacement and a BOS/CHoCH with the bias, leaving an order block or fair value gap.`,
    `Entry on ${tf.entry}m: a tap of that zone confirmed by a CHoCH. Buy ${lots} of the ${strikeLabel(o?.strike)} call (bullish) or put (bearish), ${o?.expiry === "next" ? "the expiry after the nearest" : "nearest expiry"}.`,
    `Stop beyond the sweep; TP1 1R, TP2 ${(1 + r.rr) / 2}R, TP3 ${r.rr}R${r.tranches ? ", a tranche at each" : ""}.`,
    `Entries ${s.start}–${s.last_entry}, at most ${r.max_trades_per_day} a day and ${r.max_losses_per_day} losers; exit by ${s.exit}.`,
  ];
}

/** One line for the strategies list: "NIFTY · 2 legs · 09:20–15:15". */
export function configSummary(c: StrategyConfig): string {
  if (c.kind === "rules") {
    const n = c.legs.length;
    const e = entryOf(c);
    const h = holdingOf(c);
    const hold = { intraday: "", next_day: "next day ", days: `+${h.days}d `, expiry: "expiry " }[h.mode];
    const start = e.mode === "signal" ? `signal from ${e.at}` : e.at;
    return `${c.underlying} · ${n} leg${n === 1 ? "" : "s"} · ${start}–${hold}${h.exit}`;
  }
  if (c.kind === "time_based") {
    const n = c.legs.length;
    return `${c.underlying} · ${n} leg${n === 1 ? "" : "s"} · ${c.timing?.entry ?? "09:20"}–${c.timing?.exit ?? "15:15"}`;
  }
  if (c.kind === "range_breakout") return `${c.underlying} · range ${c.range_start}–${c.range_end}`;
  if (c.kind === "smc_scalp") {
    const tf = c.timeframes;
    return `${c.underlying} · SMC ${tf?.bias ?? 15}/${tf?.setup ?? 5}/${tf?.entry ?? 1}m · 1:${c.risk?.rr ?? 2} · ${c.session?.start ?? "09:30"}–${c.session?.last_entry ?? "14:30"}`;
  }
  return `${c.underlying} · expiry days · ${c.first_entry}–${c.last_entry}`;
}

/** Next free leg id: L1, L2, ... */
export function nextLegId(legs: StrategyLeg[]): string {
  const used = new Set(legs.map((l) => l.id));
  let n = legs.length + 1;
  while (used.has(`L${n}`)) n += 1;
  return `L${n}`;
}

export function newLeg(c: { legs: StrategyLeg[] }, weekly: boolean): StrategyLeg {
  return {
    id: nextLegId(c.legs),
    action: "SELL",
    option_type: "CE",
    lots: 1,
    expiry: weekly ? "current_week" : "current_month",
    strike: { mode: "atm", offset: 0, premium: null, points: null },
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
