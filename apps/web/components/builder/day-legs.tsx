"use client";

import type { Instrument, RulesConfig, StrategyCatalog, StrategyLeg } from "@algoearning/api-types";
import { Button, Card, cn } from "@algoearning/ui";
import { Copy, Plus } from "lucide-react";
import { useState } from "react";

import { WEEKDAYS, type Weekday, newLeg } from "@/lib/strategy";

import { Check } from "./fields";
import { LegEditor } from "./leg-editor";

type Errs = Record<string, string>;
type ByDay = Partial<Record<Weekday, StrategyLeg[]>>;

const DAY_LABEL: Record<Weekday, string> = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
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

/** Different legs on different weekdays: each weekday is a normal list of legs (add, buy or sell, CE or PE, any strike
 * rule, stop-loss ... exactly like the default legs), so any shape can be built: a strangle, an iron condor, an iron fly,
 * a single leg. A weekday without legs of its own trades the default legs, or does not trade when there are none. */
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
  const dayLegs = (config.day_legs ?? {}) as ByDay;
  const open = config.day_legs != null; // the switch: an empty set is "on, no weekday set up yet"
  const [day, setDay] = useState<Weekday>("MON");
  const max = catalog.limits.max_legs;
  const taken = () => new Set([...config.legs, ...Object.values(dayLegs).flat()].map((l) => l!.id));
  const set = (next: ByDay) => onChange({ ...config, day_legs: next as RulesConfig["day_legs"] });
  const mine = dayLegs[day];

  /** Copies of `legs` for a weekday, with ids of that weekday that nobody uses yet. */
  const copies = (legs: StrategyLeg[], d: Weekday, used: Set<string>): StrategyLeg[] => {
    const ids = idsFor(d, legs.length, used);
    ids.forEach((i) => used.add(i));
    return legs.map((l, i) => ({ ...structuredClone(l), id: ids[i]! }));
  };
  const addLeg = () => {
    const used = taken();
    const [id] = idsFor(day, 1, used);
    set({
      ...dayLegs,
      [day]: [...(mine ?? []), { ...newLeg({ legs: mine ?? [] }, inst?.weekly_expiry ?? true), id: id! }],
    });
  };
  const copyTo = (targets: Weekday[]) => {
    if (!mine) return;
    const used = taken();
    const next = { ...dayLegs };
    for (const d of targets) next[d] = copies(mine, d, used);
    set(next);
  };
  const copyFrom = (from: Weekday | "default") => {
    const src = from === "default" ? config.legs : dayLegs[from];
    if (!src?.length) return;
    set({ ...dayLegs, [day]: copies(src, day, taken()) });
  };

  return (
    <Card className="flex flex-col gap-4">
      <Check
        label="Trade different legs on different weekdays"
        checked={open}
        onChange={(v) => onChange({ ...config, day_legs: v ? {} : null })}
      />
      {!open ? (
        <p className="text-sm text-muted">
          Off: the legs above trade on every entry day. Turn on to give each weekday its own legs: its own
          strikes, buy or sell, stop-loss and so on (for example sell about ₹60 premium on Monday and ₹50 on
          Tuesday).
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
              ? `A day without its own legs trades the default legs above (${config.legs.length}).`
              : "A day without legs does not trade."}
          </p>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold">
              {DAY_LABEL[day]} legs{" "}
              <span className="text-sm font-normal text-muted">
                ({mine?.length ?? 0} of {max})
              </span>
            </h3>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" disabled={(mine?.length ?? 0) >= max} onClick={addLeg}>
                <Plus className="size-4" aria-hidden /> Add leg
              </Button>
              {mine && (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => copyTo(WEEKDAYS.filter((d) => d !== day))}
                    title="Replaces the legs of the other weekdays with these"
                  >
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
                    Remove {day} legs
                  </Button>
                </>
              )}
            </div>
          </div>

          {!mine && (
            <div className="flex flex-col gap-2 rounded-xl border border-dashed border-border p-3 text-sm text-muted">
              <p>
                {DAY_LABEL[day]} has no legs of its own:{" "}
                {config.legs.length ? "it trades the default legs above." : "it does not trade."} Add a leg,
                or start from a copy:
              </p>
              <div className="flex flex-wrap gap-2">
                {config.legs.length > 0 && (
                  <Button size="sm" variant="secondary" onClick={() => copyFrom("default")}>
                    Copy the default legs
                  </Button>
                )}
                {WEEKDAYS.filter((d) => d !== day && dayLegs[d]?.length).map((d) => (
                  <Button key={d} size="sm" variant="secondary" onClick={() => copyFrom(d)}>
                    Copy {d}
                  </Button>
                ))}
              </div>
            </div>
          )}

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
              canRemove
              onChange={(l) => set({ ...dayLegs, [day]: mine.map((x, j) => (j === i ? l : x)) })}
              onRemove={() => {
                const rest = mine.filter((_, j) => j !== i);
                const next = { ...dayLegs };
                if (rest.length) next[day] = rest;
                else delete next[day]; // no legs left: the weekday is back to the default legs
                set(next);
              }}
              onDuplicate={
                mine.length < max
                  ? () => {
                      const [id] = idsFor(day, 1, taken());
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
