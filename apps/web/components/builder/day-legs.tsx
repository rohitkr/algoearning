"use client";

import type { Instrument, RulesConfig, StrategyCatalog, StrategyLeg } from "@algoearning/api-types";
import { Button, Card, cn } from "@algoearning/ui";
import { Copy } from "lucide-react";
import { useState } from "react";

import { WEEKDAYS, type Weekday } from "@/lib/strategy";

import { Check, NumberField, SelectField } from "./fields";
import { LegEditor } from "./leg-editor";

type Errs = Record<string, string>;
type Shape = "strangle" | "straddle" | "condor" | "fly";
type By = "premium" | "points";

const DAY_LABEL: Record<Weekday, string> = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
};
const SHAPE_LABEL: Record<Shape, string> = {
  strangle: "Sell CE + PE (strangle)",
  straddle: "Sell ATM CE + PE (straddle)",
  condor: "Iron condor",
  fly: "Iron fly",
};

/** Leg ids stay unique across the whole config: MON1, MON2, ... (at most 12 characters). */
function idsFor(day: Weekday, n: number, taken: Set<string>): string[] {
  const out: string[] = [];
  let k = 1;
  while (out.length < n) {
    const id = `${day}${k++}`;
    if (!taken.has(id)) out.push(id);
  }
  return out;
}

type Build = { shape: Shape; by: By; sell: number; wing: number; lots: number };

/** The legs of one day for a shape: what to sell (and, for a condor or fly, the wings to buy). Premium: the strike
 * whose premium is nearest the number; points: that many index points out of the money (wings: from the index). */
function buildLegs(day: Weekday, b: Build, expiry: StrategyLeg["expiry"], taken: Set<string>): StrategyLeg[] {
  const strike = (v: number, atm = false): StrategyLeg["strike"] =>
    atm
      ? { mode: "atm", offset: 0, premium: null, points: null }
      : b.by === "premium"
        ? { mode: "premium", offset: 0, premium: v, points: null }
        : { mode: "points", offset: 0, premium: null, points: v };
  const leg = (id: string, action: "SELL" | "BUY", option_type: "CE" | "PE", s: StrategyLeg["strike"]): StrategyLeg => ({
    id, action, option_type, lots: b.lots, expiry, strike: s, direction: "always",
    stop_loss: null, target: null, trailing: null, reentry_on_sl: null, reentry_on_target: null,
  }); // prettier-ignore
  const flat = b.shape === "straddle" || b.shape === "fly";
  const body = strike(b.sell, flat);
  const wings = b.shape === "condor" || b.shape === "fly";
  const ids = idsFor(day, wings ? 4 : 2, taken);
  const legs = [leg(ids[0]!, "SELL", "CE", body), leg(ids[1]!, "SELL", "PE", body)];
  if (wings) legs.push(leg(ids[2]!, "BUY", "CE", strike(b.wing)), leg(ids[3]!, "BUY", "PE", strike(b.wing)));
  return legs;
}

/** Different legs on different weekdays (a strangle on Monday, an iron condor on Tuesday ...), each with its own strikes.
 * A weekday without its own legs trades the default legs above. */
export function DayLegs({
  config,
  catalog,
  inst,
  errs,
  warns,
  onChange,
}: {
  config: RulesConfig;
  catalog: StrategyCatalog;
  inst: Instrument | undefined;
  errs: Errs;
  warns: Errs;
  onChange: (c: RulesConfig) => void;
}) {
  const dayLegs = (config.day_legs ?? {}) as Partial<Record<Weekday, StrategyLeg[]>>;
  const open = config.day_legs != null; // the switch: an empty set is "on, no weekday set up yet"
  const [day, setDay] = useState<Weekday>("MON");
  const [build, setBuild] = useState<Build>({
    shape: "strangle",
    by: "premium",
    sell: 60,
    wing: 10,
    lots: 1,
  });
  const expiry: StrategyLeg["expiry"] = inst?.weekly_expiry === false ? "current_month" : "current_week";
  const taken = new Set([...config.legs, ...Object.values(dayLegs).flat()].map((l) => l!.id));
  const set = (next: Partial<Record<Weekday, StrategyLeg[]>>) =>
    onChange({ ...config, day_legs: next as RulesConfig["day_legs"] });
  const mine = dayLegs[day];
  const max = catalog.limits.max_legs;
  const wings = build.shape === "condor" || build.shape === "fly";
  const flat = build.shape === "straddle" || build.shape === "fly";
  const unit = build.by === "premium" ? "₹ premium" : "pts from the index";

  function copyToOthers() {
    if (!mine) return;
    const used = new Set(taken);
    const next = { ...dayLegs };
    for (const d of WEEKDAYS) {
      if (d === day) continue;
      const ids = idsFor(d, mine.length, used);
      ids.forEach((i) => used.add(i));
      next[d] = mine.map((l, i) => ({ ...structuredClone(l), id: ids[i]! }));
    }
    set(next);
  }

  return (
    <Card className="flex flex-col gap-4">
      <Check
        label="Trade different legs on different weekdays"
        checked={open}
        onChange={(v) => onChange({ ...config, day_legs: v ? {} : null })}
      />
      {!open ? (
        <p className="text-sm text-muted">
          Off: the legs above trade on every entry day. Turn on to give a weekday its own legs and strikes
          (for example sell about ₹60 premium on Monday and ₹50 on Tuesday, or an iron condor on Wednesday).
        </p>
      ) : (
        <>
          <div role="tablist" aria-label="Weekday" className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((d) => (
              <button
                key={d}
                type="button"
                role="tab"
                aria-selected={d === day}
                onClick={() => setDay(d)}
                className={cn(
                  "h-8 rounded-lg border px-3 text-xs font-semibold",
                  d === day
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border text-muted hover:bg-surface-2",
                )}
              >
                {d}
                {dayLegs[d] ? " ●" : ""}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted">
            ● = has its own legs.{" "}
            {config.legs.length
              ? `Other days trade the default legs above (${config.legs.length}).`
              : "A day without legs does not trade."}
          </p>

          <div className="flex flex-col gap-3 rounded-xl border border-border p-3">
            <p className="text-sm font-medium">
              {mine ? `${DAY_LABEL[day]}: replace with a new set` : `${DAY_LABEL[day]}: set up legs`}
            </p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <SelectField<Shape>
                label="Trade"
                value={build.shape}
                options={(Object.keys(SHAPE_LABEL) as Shape[]).map((v) => ({
                  value: v,
                  label: SHAPE_LABEL[v],
                }))}
                onChange={(shape) => setBuild({ ...build, shape })}
              />
              <SelectField<By>
                label="Pick strikes by"
                value={build.by}
                options={[
                  { value: "premium", label: "Premium (₹)" },
                  { value: "points", label: "Points from index" },
                ]}
                onChange={(by) => setBuild({ ...build, by })}
              />
              {!flat && (
                <NumberField
                  label={`Sell near (${unit})`}
                  value={build.sell}
                  step={build.by === "premium" ? 1 : 50}
                  onChange={(v) => setBuild({ ...build, sell: v ?? 0 })}
                />
              )}
              {wings && (
                <NumberField
                  label={`Buy wings near (${unit})`}
                  value={build.wing}
                  step={build.by === "premium" ? 1 : 50}
                  onChange={(v) => setBuild({ ...build, wing: v ?? 0 })}
                />
              )}
              <NumberField
                label="Lots per leg"
                value={build.lots}
                onChange={(v) => setBuild({ ...build, lots: Math.max(1, v ?? 1) })}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() =>
                  set({
                    ...dayLegs,
                    [day]: buildLegs(
                      day,
                      build,
                      expiry,
                      new Set([...taken].filter((id) => !mine?.some((l) => l.id === id))),
                    ),
                  })
                }
              >
                {mine ? `Replace ${day} legs` : `Add ${day} legs`}
              </Button>
              {mine && (
                <>
                  <Button size="sm" variant="secondary" onClick={copyToOthers}>
                    <Copy className="size-4" aria-hidden /> Copy to the other weekdays
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      const next = { ...dayLegs };
                      delete next[day];
                      set(next);
                    }}
                  >
                    Use the default legs on {day}
                  </Button>
                </>
              )}
            </div>
          </div>

          {mine?.map((leg, i) => (
            <LegEditor
              key={leg.id}
              leg={leg}
              index={i}
              path={`day_legs.${day}.${i}`}
              instrument={inst}
              maxOffset={catalog.limits.max_strike_offset}
              errs={errs}
              warns={warns}
              canRemove={mine.length > 1}
              onChange={(l) => set({ ...dayLegs, [day]: mine.map((x, j) => (j === i ? l : x)) })}
              onRemove={() => set({ ...dayLegs, [day]: mine.filter((_, j) => j !== i) })}
              onDuplicate={
                mine.length < max
                  ? () => {
                      const [id] = idsFor(day, 1, taken);
                      set({
                        ...dayLegs,
                        [day]: [
                          ...mine.slice(0, i + 1),
                          { ...structuredClone(leg), id: id! },
                          ...mine.slice(i + 1),
                        ],
                      });
                    }
                  : undefined
              }
            />
          ))}
          {errs["day_legs"] && <p className="text-sm text-loss">{errs["day_legs"]}</p>}
        </>
      )}
    </Card>
  );
}
