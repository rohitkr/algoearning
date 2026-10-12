"use client";

import { useRemembered } from "@/lib/use-remembered";
import type { Instrument, StrategyCatalog, StrategyLeg, TradePlan } from "@algoearning/api-types";
import { Button, Card, cn } from "@algoearning/ui";
import { Plus } from "lucide-react";
import type { ReactNode } from "react";

import {
  DIRECTION_LABEL,
  HOLD_LABEL,
  type Weekday,
  WEEKDAYS,
  entryOf,
  exitOf,
  holdingOf,
  indicatorOperand,
  newLeg,
  operand,
  rulesRiskOf,
} from "@/lib/strategy";

import { ConditionGroupEditor, blankCondition } from "./condition-editor";
import { Check, NumberField, SelectField, TimeField } from "./fields";
import { LegEditor } from "./leg-editor";
import { TIP_SETUPS, TipEntry } from "./tip-entry";

type Errs = Record<string, string>;
type Signal = NonNullable<NonNullable<TradePlan["entry"]>["signals"]>[number];

const DTE_CHOICES = [0, 1, 2, 3, 4, 5, 6] as const;

/** A row of on/off chips (weekdays, days before expiry). */
export function Chips<T extends string | number>({
  id,
  label,
  values,
  selected,
  render,
  onToggle,
  error,
}: {
  id: string;
  label: string;
  values: readonly T[];
  selected: readonly T[];
  render: (v: T) => ReactNode;
  onToggle: (v: T, on: boolean) => void;
  error?: string;
}) {
  return (
    <div>
      <p className="text-xs font-medium text-muted" id={id}>
        {label}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-labelledby={id}>
        {values.map((v) => {
          const on = selected.includes(v);
          return (
            <button
              key={String(v)}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(v, !on)}
              className={cn(
                "h-8 min-w-12 rounded-lg border px-2 text-xs font-semibold",
                on
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border text-muted hover:bg-surface-2",
              )}
            >
              {render(v)}
            </button>
          );
        })}
      </div>
      {error && <p className="mt-1 text-xs text-loss">{error}</p>}
    </div>
  );
}

/** Step heading: a number, what it decides, and one line saying how. */
function Step({ n, title, hint }: { n: number; title: string; hint: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
        {n}
      </span>
      <div>
        <h3 className="font-semibold">{title}</h3>
        <p className="text-sm text-muted">{hint}</p>
      </div>
    </div>
  );
}

const cond = (
  left: ReturnType<typeof operand>,
  op: "crosses_above" | "crosses_below" | "above" | "below",
  right: ReturnType<typeof operand>,
  candle: 1 | 3 | 5 | 15 | 30 | 60 = 5,
) => ({ left, op, right, candle });
const price = () => operand({ kind: "price" });
const level = (l: NonNullable<ReturnType<typeof operand>["level"]>) => operand({ kind: "level", level: l });
const ind = (name: Parameters<typeof indicatorOperand>[0], period: number) => ({
  ...indicatorOperand(name),
  period,
});
const num = (v: number) => operand({ kind: "number", value: v });
const signal = (direction: "up" | "down", c: ReturnType<typeof cond>): Signal => ({
  direction,
  match: "all",
  conditions: [c],
});

/** One-click starting points for "when conditions are met": breakout / breakdown and indicator crossovers. */
export const SIGNAL_STARTS: Record<string, { label: string; signals: Signal[] }> = {
  orb: {
    label: "Opening range breakout / breakdown",
    signals: [
      signal("up", cond(price(), "crosses_above", level("opening_high"))),
      signal("down", cond(price(), "crosses_below", level("opening_low"))),
    ],
  },
  prev: {
    label: "Previous day high / low break",
    signals: [
      signal("up", cond(price(), "crosses_above", level("prev_high"))),
      signal("down", cond(price(), "crosses_below", level("prev_low"))),
    ],
  },
  ema: {
    label: "EMA 9 / 21 crossover",
    signals: [
      signal("up", cond(ind("ema", 9), "crosses_above", ind("ema", 21))),
      signal("down", cond(ind("ema", 9), "crosses_below", ind("ema", 21))),
    ],
  },
  rsi: {
    label: "RSI(14) 30 / 70",
    signals: [
      signal("up", cond(ind("rsi", 14), "crosses_above", num(30))),
      signal("down", cond(ind("rsi", 14), "crosses_below", num(70))),
    ],
  },
  supertrend: {
    label: "Supertrend flip",
    signals: [
      signal("up", cond(price(), "crosses_above", ind("supertrend", 10))),
      signal("down", cond(price(), "crosses_below", ind("supertrend", 10))),
    ],
  },
};

/**
 * One plan, top to bottom: 1 how a trade starts, 2 what it trades, 3 how it ends. Everything about the plan is here once
 * (its weekdays and instrument are in the plan's header). `errs` are the plan's own, relative to the plan.
 */
export function PlanEditor({
  plan,
  catalog,
  inst,
  hours,
  errs,
  warns,
  onChange,
}: {
  plan: TradePlan;
  catalog: StrategyCatalog;
  inst: Instrument | undefined;
  hours: { min?: string; max?: string };
  errs: Errs;
  warns: Errs;
  onChange: (p: TradePlan) => void;
}) {
  const e = entryOf(plan);
  const h = holdingOf(plan);
  const r = rulesRiskOf(plan);
  const ex = exitOf(plan);
  const signals = e.signals ?? [];
  const byConditions = e.mode === "conditions";
  const byTip = e.mode === "tip";
  const directional = byConditions || byTip;
  const lastLock = useRemembered(r.lock_profit);
  const lastCombined = useRemembered(r.combined_stop);
  const lastUntil = useRemembered(e.until);
  const lastWhen = useRemembered(ex.when);
  const setEntry = (p: Partial<typeof e>) => onChange({ ...plan, entry: { ...e, ...p } });
  const setHolding = (p: Partial<typeof h>) => onChange({ ...plan, holding: { ...h, ...p } });
  const setRisk = (p: Partial<typeof r>) => onChange({ ...plan, risk: { ...r, ...p } });
  const setExit = (p: Partial<typeof ex>) => onChange({ ...plan, exit: { ...ex, ...p } });
  const setSignals = (next: Signal[]) => setEntry({ signals: next });
  const legs = plan.legs;
  const max = catalog.limits.max_legs;
  const withLegs = (next: StrategyLeg[]) => onChange({ ...plan, legs: next });

  return (
    <div className="flex flex-col gap-4">
      {/* 1 · ENTRY */}
      <Card className="flex flex-col gap-4">
        <Step n={1} title="Entry" hint="When a trade starts." />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SelectField
            label="Enter"
            value={e.mode}
            error={errs["entry.mode"]}
            options={[
              { value: "time", label: "At a time" },
              { value: "conditions", label: "When conditions are met" },
              { value: "tip", label: "On a Telegram tip" },
            ]}
            onChange={(mode) =>
              setEntry(
                mode === "conditions"
                  ? {
                      mode,
                      at: e.at < "09:30" ? "09:30" : e.at,
                      signals: signals.length
                        ? signals
                        : [{ direction: "up", match: "all", conditions: [blankCondition()] }],
                    }
                  : { mode, signals: [] },
              )
            }
          />
          <TimeField
            label={directional ? "Earliest entry" : "Entry time"}
            value={e.at}
            {...hours}
            error={errs["entry.at"]}
            onChange={(v) => setEntry({ at: v })}
          />
          {directional && (
            <>
              <div className="flex flex-col gap-2">
                <Check
                  label="No new trade after"
                  checked={e.until != null}
                  error={errs["entry.until"]}
                  onChange={(on) =>
                    setEntry({
                      until: on
                        ? (lastUntil ?? (h.mode === "intraday" ? h.exit : (hours.max ?? e.at)))
                        : null,
                    })
                  }
                />
                {e.until != null && (
                  <TimeField label="" value={e.until} {...hours} onChange={(v) => setEntry({ until: v })} />
                )}
              </div>
              <NumberField
                label="Trades a day (at most)"
                value={e.max_per_day}
                min={1}
                max={10}
                error={errs["entry.max_per_day"]}
                onChange={(v) => setEntry({ max_per_day: v ?? Number.NaN })}
              />
            </>
          )}
        </div>
        {h.mode !== "intraday" && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <NumberField
              label="Trades open at once (at most)"
              value={plan.max_open ?? 1}
              min={1}
              max={10}
              error={errs["max_open"]}
              onChange={(v) => onChange({ ...plan, max_open: v ?? Number.NaN })}
            />
            <p className="col-span-2 self-end text-xs text-muted sm:col-span-3">
              {(plan.max_open ?? 1) === 1
                ? "1: a new trade waits until the held one is closed, so an entry that falls on a day a trade is still open is skipped."
                : `Up to ${plan.max_open}: each day's entry runs even while earlier trades are still held; a day with ${plan.max_open} already open is skipped.`}
            </p>
          </div>
        )}
        <details className="text-sm" open={!!e.dte?.length}>
          <summary className="cursor-pointer text-muted">More: only on certain days before expiry</summary>
          <div className="mt-3">
            <Chips
              id={`dte-${plan.id}`}
              label="Only on these trading days before expiry (none selected = any day)"
              values={DTE_CHOICES}
              selected={e.dte ?? []}
              render={(n) => (n === 0 ? "Expiry" : `${n}d`)}
              error={errs["entry.dte"]}
              onToggle={(n, on) => {
                const next = on
                  ? [...(e.dte ?? []), n].sort((a, b) => a - b)
                  : (e.dte ?? []).filter((x) => x !== n);
                setEntry({ dte: next.length ? next : null });
              }}
            />
          </div>
        </details>
        {byTip && <TipEntry config={plan} errs={errs} onChange={onChange} />}
        {byConditions && (
          <section className="flex flex-col gap-3" aria-label="Signals">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-muted">
                A trade starts on the first signal that is true. Conditions are checked when a candle
                finishes.
              </p>
              <Button
                size="sm"
                variant="secondary"
                disabled={signals.length >= 4}
                onClick={() =>
                  setSignals([
                    ...signals,
                    {
                      direction: signals.some((x) => x.direction === "up") ? "down" : "up",
                      match: "all",
                      conditions: [blankCondition()],
                    },
                  ])
                }
              >
                <Plus className="size-4" aria-hidden /> Add signal
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">Start from:</span>
              {Object.entries(SIGNAL_STARTS).map(([k, s]) => (
                <Button
                  key={k}
                  size="sm"
                  variant="secondary"
                  onClick={() => setSignals(structuredClone(s.signals))}
                >
                  {s.label}
                </Button>
              ))}
            </div>
            {errs["entry.signals"] && <p className="text-sm text-loss">{errs["entry.signals"]}</p>}
            {signals.map((sg, i) => (
              <div key={i} className="flex flex-col gap-3 rounded-xl border border-border p-3">
                <div className="flex flex-wrap items-end gap-3">
                  <SelectField
                    label={`Signal ${i + 1} says`}
                    className="sm:w-48"
                    value={sg.direction}
                    error={errs[`entry.signals.${i}.direction`]}
                    options={(Object.keys(DIRECTION_LABEL) as (keyof typeof DIRECTION_LABEL)[]).map((d) => ({
                      value: d,
                      label: DIRECTION_LABEL[d],
                    }))}
                    onChange={(direction) =>
                      setSignals(signals.map((x, j) => (j === i ? { ...x, direction } : x)))
                    }
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setSignals(signals.filter((_, j) => j !== i))}
                    aria-label={`Remove signal ${i + 1}`}
                  >
                    Remove signal
                  </Button>
                </div>
                <ConditionGroupEditor
                  group={sg}
                  errs={errs}
                  path={`entry.signals.${i}`}
                  onChange={(g) => setSignals(signals.map((x, j) => (j === i ? { ...x, ...g } : x)))}
                />
              </div>
            ))}
          </section>
        )}
      </Card>

      {/* 2 · LEGS */}
      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <Step
            n={2}
            title="Legs"
            hint={
              directional
                ? "What to trade. A leg marked Up trades on an up signal, Down on a down signal."
                : "What to trade: buy or sell, CE or PE, any strike. Each leg has its own stop-loss, target and trailing."
            }
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={legs.length >= max}
            onClick={() => withLegs([...legs, newLeg(plan, inst?.weekly_expiry ?? true)])}
          >
            <Plus className="size-4" aria-hidden /> Add leg{" "}
            <span className="text-xs text-muted">
              ({legs.length} of {max})
            </span>
          </Button>
        </div>
        {directional && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">Quick legs:</span>
            {Object.entries(TIP_SETUPS).map(([k, s]) => (
              <Button key={k} size="sm" variant="secondary" onClick={() => withLegs(structuredClone(s.legs))}>
                {s.label}
              </Button>
            ))}
          </div>
        )}
        {errs.legs && <p className="text-sm text-loss">{errs.legs}</p>}
        {legs.map((leg, i) => (
          <LegEditor
            key={leg.id + i}
            leg={leg}
            index={i}
            instrument={inst}
            maxOffset={catalog.limits.max_strike_offset}
            errs={errs}
            warns={warns}
            canRemove={legs.length > 1}
            directional={directional}
            onChange={(l) => withLegs(legs.map((x, j) => (j === i ? l : x)))}
            onDuplicate={
              legs.length < max
                ? () => {
                    const copy = { ...legs[i]!, id: newLeg(plan, true).id };
                    withLegs([...legs.slice(0, i + 1), copy, ...legs.slice(i + 1)]);
                  }
                : undefined
            }
            onRemove={() => withLegs(legs.filter((_, j) => j !== i))}
          />
        ))}
      </Card>

      {/* 3 · EXIT */}
      <Card className="flex flex-col gap-4">
        <Step
          n={3}
          title="Exit"
          hint="How the trade ends: at a time, on the whole trade's profit or loss, or on a condition. Each leg's own stop-loss, target and trailing (step 2) also apply."
        />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SelectField
            label="Hold"
            value={h.mode}
            error={errs["holding.mode"]}
            options={(Object.keys(HOLD_LABEL) as (keyof typeof HOLD_LABEL)[]).map((m) => ({
              value: m,
              label: HOLD_LABEL[m],
            }))}
            onChange={(mode) => setHolding({ mode })}
          />
          <TimeField
            label={h.mode === "intraday" ? "Exit time" : "Exit time on that day"}
            value={h.exit}
            {...hours}
            error={errs["holding.exit"]}
            onChange={(v) => setHolding({ exit: v })}
          />
          {h.mode === "days" && (
            <NumberField
              label="Trading days"
              value={h.days}
              min={1}
              max={30}
              error={errs["holding.days"]}
              onChange={(v) => setHolding({ days: v ?? Number.NaN })}
            />
          )}
        </div>
        {h.mode !== "intraday" && (
          <p className="text-xs text-muted">
            Held overnight, so orders use the NRML product and need overnight margin. Legs must expire on or
            after the exit day.
          </p>
        )}

        <div className="flex flex-col gap-4 border-t border-border pt-4">
          <div>
            <h4 className="text-sm font-semibold">Whole-trade limits</h4>
            <p className="text-sm text-muted">
              On all legs together, from entry to the final exit (overnight included). Leave empty for none.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <NumberField
              label="Max loss"
              value={r.mtm_stop_loss}
              optional
              min={0}
              suffix="₹"
              error={errs["risk.mtm_stop_loss"]}
              onChange={(v) => setRisk({ mtm_stop_loss: v })}
            />
            <NumberField
              label="Profit target"
              value={r.mtm_target}
              optional
              min={0}
              suffix="₹"
              error={errs["risk.mtm_target"]}
              onChange={(v) => setRisk({ mtm_target: v })}
            />
          </div>
          <Check
            label="When any leg's stop-loss hits, exit every leg"
            checked={!!r.exit_all_on_leg_sl}
            error={errs["risk.exit_all_on_leg_sl"]}
            onChange={(on) => setRisk({ exit_all_on_leg_sl: on })}
          />
          <div
            className={cn(
              "rounded-lg border border-border p-3",
              r.lock_profit ? "bg-surface" : "bg-surface-2/50",
            )}
          >
            <Check
              label="Lock profit (and trail it)"
              checked={!!r.lock_profit}
              error={errs["risk.lock_profit"]}
              onChange={(on) =>
                setRisk({
                  lock_profit: on
                    ? (lastLock ?? { at: 2000, lock: 1000, trail_every: null, trail_by: null })
                    : null,
                })
              }
            />
            {r.lock_profit && (
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <NumberField
                  label="When profit reaches"
                  value={r.lock_profit.at}
                  min={0}
                  suffix="₹"
                  error={errs["risk.lock_profit.at"]}
                  onChange={(v) =>
                    r.lock_profit && setRisk({ lock_profit: { ...r.lock_profit, at: v ?? Number.NaN } })
                  }
                />
                <NumberField
                  label="Keep at least"
                  value={r.lock_profit.lock}
                  min={0}
                  suffix="₹"
                  error={errs["risk.lock_profit.lock"]}
                  onChange={(v) =>
                    r.lock_profit && setRisk({ lock_profit: { ...r.lock_profit, lock: v ?? Number.NaN } })
                  }
                />
                <NumberField
                  label="Then every"
                  value={r.lock_profit.trail_every}
                  optional
                  min={0}
                  suffix="₹"
                  error={errs["risk.lock_profit.trail_every"]}
                  onChange={(v) =>
                    r.lock_profit && setRisk({ lock_profit: { ...r.lock_profit, trail_every: v } })
                  }
                />
                <NumberField
                  label="Raise it by"
                  value={r.lock_profit.trail_by}
                  optional
                  min={0}
                  suffix="₹"
                  error={errs["risk.lock_profit.trail_by"]}
                  onChange={(v) =>
                    r.lock_profit && setRisk({ lock_profit: { ...r.lock_profit, trail_by: v } })
                  }
                />
              </div>
            )}
          </div>
          <div
            className={cn(
              "rounded-lg border border-border p-3",
              r.combined_stop ? "bg-surface" : "bg-surface-2/50",
            )}
          >
            <Check
              label="Combined premium stop (sold legs together)"
              checked={!!r.combined_stop}
              error={errs["risk.combined_stop"]}
              onChange={(on) =>
                setRisk({ combined_stop: on ? (lastCombined ?? { unit: "percent", value: 30 }) : null })
              }
            />
            {r.combined_stop && (
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
                <NumberField
                  label="Exit when up by"
                  value={r.combined_stop.value}
                  min={0}
                  suffix={r.combined_stop.unit === "percent" ? "%" : "pts"}
                  error={errs["risk.combined_stop.value"]}
                  onChange={(v) =>
                    r.combined_stop &&
                    setRisk({ combined_stop: { ...r.combined_stop, value: v ?? Number.NaN } })
                  }
                />
                <SelectField
                  label="In"
                  value={r.combined_stop.unit}
                  options={[
                    { value: "percent", label: "Percent" },
                    { value: "points", label: "Points" },
                  ]}
                  onChange={(unit) =>
                    r.combined_stop && setRisk({ combined_stop: { ...r.combined_stop, unit } })
                  }
                />
              </div>
            )}
          </div>
        </div>

        {byConditions && (
          <div className="flex flex-col gap-3 border-t border-border pt-4">
            <div>
              <h4 className="text-sm font-semibold">Exit on conditions</h4>
              <p className="text-sm text-muted">Close the whole trade when these hold, besides the above.</p>
            </div>
            <Check
              label="Exit when a signal comes in the other direction"
              checked={ex.on_opposite_signal}
              error={errs["exit.on_opposite_signal"]}
              onChange={(on) => setExit({ on_opposite_signal: on })}
            />
            <Check
              label="Exit when a condition is true"
              checked={!!ex.when}
              onChange={(on) =>
                setExit({
                  when: on
                    ? (lastWhen ?? {
                        match: "all",
                        conditions: [{ ...blankCondition(), op: "below", right: level("opening_high") }],
                      })
                    : null,
                })
              }
            />
            {ex.when && (
              <ConditionGroupEditor
                group={ex.when}
                errs={errs}
                path="exit.when"
                onChange={(g) => setExit({ when: g })}
              />
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

export { WEEKDAYS };
export type { Weekday };
