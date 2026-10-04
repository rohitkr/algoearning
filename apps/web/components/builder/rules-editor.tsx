"use client";

import type { Instrument, RulesConfig, StrategyCatalog } from "@algoearning/api-types";
import { Button, Card, cn } from "@algoearning/ui";
import { Plus } from "lucide-react";
import type { ReactNode } from "react";

import {
  DIRECTION_LABEL,
  HOLD_LABEL,
  WEEKDAYS,
  type Weekday,
  entryOf,
  exitOf,
  holdingOf,
  newLeg,
  rulesRiskOf,
} from "@/lib/strategy";

import { ConditionGroupEditor, blankCondition } from "./condition-editor";
import { Check, NumberField, SelectField, TimeField } from "./fields";
import { LegEditor } from "./leg-editor";

type Errs = Record<string, string>;

const DTE_CHOICES = [0, 1, 2, 3, 4, 5, 6] as const;

/** A row of on/off chips (weekdays, days before expiry). */
function Chips<T extends string | number>({
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

/** The builder's form for a `rules` config (ADR 0022): when to enter, how long to hold, the legs, and trade limits. */
export function RulesEditor({
  config,
  catalog,
  inst,
  hours,
  errs,
  warns,
  onChange,
}: {
  config: RulesConfig;
  catalog: StrategyCatalog;
  inst: Instrument | undefined;
  hours: { min?: string; max?: string };
  errs: Errs;
  warns: Errs;
  onChange: (c: RulesConfig) => void;
}) {
  const e = entryOf(config);
  const h = holdingOf(config);
  const r = rulesRiskOf(config);
  const ex = exitOf(config);
  const signals = e.signals ?? [];
  const byConditions = e.mode === "conditions";
  const days = (e.days ?? [...WEEKDAYS]) as Weekday[];
  const setEntry = (p: Partial<typeof e>) => onChange({ ...config, entry: { ...e, ...p } });
  const setHolding = (p: Partial<typeof h>) => onChange({ ...config, holding: { ...h, ...p } });
  const setRisk = (p: Partial<typeof r>) => onChange({ ...config, risk: { ...r, ...p } });
  const setExit = (p: Partial<typeof ex>) => onChange({ ...config, exit: { ...ex, ...p } });
  const setSignals = (next: typeof signals) => setEntry({ signals: next });
  const legs = config.legs;
  const max = catalog.limits.max_legs;

  return (
    <>
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-semibold">Schedule</h2>
          <p className="text-sm text-muted">
            When a trade starts and how long it is held. One trade at a time.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SelectField
            label="Enter"
            value={e.mode}
            error={errs["entry.mode"]}
            options={[
              { value: "time", label: "At a time" },
              { value: "conditions", label: "When conditions are met" },
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
            label={byConditions ? "Earliest entry" : "Entry time"}
            value={e.at}
            {...hours}
            error={errs["entry.at"]}
            onChange={(v) => setEntry({ at: v })}
          />
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
          {h.mode === "days" ? (
            <NumberField
              label="Trading days"
              value={h.days}
              min={1}
              max={30}
              error={errs["holding.days"]}
              onChange={(v) => setHolding({ days: v ?? Number.NaN })}
            />
          ) : (
            <div className="hidden sm:block" />
          )}
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <Check
            label="Stop entering after a time"
            checked={e.until != null}
            error={errs["entry.until"]}
            onChange={(on) =>
              setEntry({ until: on ? (h.mode === "intraday" ? h.exit : (hours.max ?? e.at)) : null })
            }
          />
          {e.until != null && (
            <TimeField
              label="No entry from"
              className="sm:w-40"
              value={e.until}
              {...hours}
              onChange={(v) => setEntry({ until: v })}
            />
          )}
        </div>
        {byConditions && (
          <NumberField
            label="Trades a day (at most)"
            value={e.max_per_day}
            min={1}
            max={10}
            className="sm:w-48"
            error={errs["entry.max_per_day"]}
            onChange={(v) => setEntry({ max_per_day: v ?? Number.NaN })}
          />
        )}
        <Chips
          id="days-label"
          label="Trade on"
          values={WEEKDAYS}
          selected={days}
          render={(d) => d.slice(0, 1) + d.slice(1).toLowerCase()}
          error={errs["entry.days"]}
          onToggle={(d, on) =>
            setEntry({
              days: on ? WEEKDAYS.filter((x) => x === d || days.includes(x)) : days.filter((x) => x !== d),
            })
          }
        />
        <Chips
          id="dte-label"
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
        {h.mode !== "intraday" && (
          <p className="text-xs text-muted">
            Held overnight, so orders use the NRML product and need overnight margin. Legs must expire on or
            after the exit day.
          </p>
        )}
      </Card>

      {byConditions && (
        <section className="flex flex-col gap-3" aria-label="Signals">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-semibold">Signals</h2>
              <p className="text-sm text-muted">
                A trade starts on the first signal that is true. Conditions are checked when a candle
                finishes.
              </p>
            </div>
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
          {errs["entry.signals"] && <p className="text-sm text-loss">{errs["entry.signals"]}</p>}
          {signals.map((sg, i) => (
            <Card key={i} className="flex flex-col gap-3">
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
            </Card>
          ))}
        </section>
      )}

      <section className="flex flex-col gap-3" aria-label="Legs">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">
            Legs{" "}
            <span className="text-sm font-normal text-muted">
              ({legs.length} of {max})
            </span>
          </h2>
          <Button
            size="sm"
            variant="secondary"
            disabled={legs.length >= max}
            onClick={() =>
              onChange({ ...config, legs: [...legs, newLeg(config, inst?.weekly_expiry ?? true)] })
            }
          >
            <Plus className="size-4" aria-hidden /> Add leg
          </Button>
        </div>
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
            directional={byConditions}
            onChange={(l) => onChange({ ...config, legs: legs.map((x, j) => (j === i ? l : x)) })}
            onDuplicate={
              legs.length < max
                ? () => {
                    const copy = { ...legs[i]!, id: newLeg(config, true).id };
                    onChange({ ...config, legs: [...legs.slice(0, i + 1), copy, ...legs.slice(i + 1)] });
                  }
                : undefined
            }
            onRemove={() => onChange({ ...config, legs: legs.filter((_, j) => j !== i) })}
          />
        ))}
      </section>

      {byConditions && (
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="font-semibold">Exit on conditions</h2>
            <p className="text-sm text-muted">
              Close the whole trade when these hold, besides stops and the exit time.
            </p>
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
                  ? {
                      match: "all",
                      conditions: [
                        {
                          ...blankCondition(),
                          op: "below",
                          right: { kind: "level", level: "opening_high", minutes: 15 },
                        },
                      ],
                    }
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
        </Card>
      )}

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-semibold">Trade risk</h2>
          <p className="text-sm text-muted">
            Limits on all legs together, from entry to the final exit (overnight included). Leave empty for
            none.
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
            r.combined_stop ? "bg-surface" : "bg-surface-2/50",
          )}
        >
          <Check
            label="Combined premium stop (sold legs together)"
            checked={!!r.combined_stop}
            error={errs["risk.combined_stop"]}
            onChange={(on) => setRisk({ combined_stop: on ? { unit: "percent", value: 30 } : null })}
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
        <div
          className={cn(
            "rounded-lg border border-border p-3",
            r.lock_profit ? "bg-surface" : "bg-surface-2/50",
          )}
        >
          <Check
            label="Lock profit"
            checked={!!r.lock_profit}
            error={errs["risk.lock_profit"]}
            onChange={(on) =>
              setRisk({
                lock_profit: on ? { at: 2000, lock: 1000, trail_every: null, trail_by: null } : null,
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
                onChange={(v) => r.lock_profit && setRisk({ lock_profit: { ...r.lock_profit, trail_by: v } })}
              />
            </div>
          )}
        </div>
      </Card>
    </>
  );
}
