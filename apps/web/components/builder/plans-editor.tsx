"use client";

import type { PlansConfig, StrategyCatalog, TradePlan } from "@algoearning/api-types";
import { Button, Card, cn } from "@algoearning/ui";
import { Copy, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { WEEKDAYS, type Weekday, newPlan, nextPlanId } from "@/lib/strategy";

import { Field, SelectField, inputClass } from "./fields";
import { Chips, PlanEditor } from "./plan-editor";

type Errs = Record<string, string>;
const MAX_PLANS = 12;
const short = (d: string) => d.slice(0, 1) + d.slice(1).toLowerCase();

/** The errors of plan `i`, keyed relative to the plan ("plans.2.entry.at" -> "entry.at"). */
export function scoped(errs: Errs, i: number): Errs {
  const prefix = `plans.${i}.`;
  return Object.fromEntries(
    Object.entries(errs)
      .filter(([k]) => k.startsWith(prefix))
      .map(([k, v]) => [k.slice(prefix.length), v]),
  );
}

/**
 * A strategy as day plans (ADR 0026): each plan has its own weekdays, instrument, entry, legs and exit. Plans may share a
 * day: they run side by side. The week strip shows who trades when.
 */
export function PlansEditor({
  config,
  catalog,
  errs,
  warns,
  onChange,
}: {
  config: PlansConfig;
  catalog: StrategyCatalog;
  errs: Errs;
  warns: Errs;
  onChange: (c: PlansConfig) => void;
}) {
  const plans = config.plans;
  const [current, setCurrent] = useState(0);
  const at = Math.min(current, plans.length - 1);
  const plan = plans[at]!;
  const inst = catalog.instruments.find((i) => i.code === plan.underlying);
  const hours = { min: inst?.session_open, max: inst?.session_close };
  const planErrs = scoped(errs, at);
  const planWarns = scoped(warns, at);
  const withPlans = (next: TradePlan[]) =>
    onChange({ ...config, plans: next, underlying: next[0]?.underlying ?? config.underlying }); // the API derives it too
  const setPlan = (p: TradePlan) => withPlans(plans.map((x, j) => (j === at ? p : x)));

  const covered = useMemo(
    () =>
      Object.fromEntries(
        WEEKDAYS.map((d) => [
          d,
          plans.filter((p) => (p.days ?? [...WEEKDAYS]).includes(d)).map((p) => p.name),
        ]),
      ) as Record<Weekday, string[]>,
    [plans],
  );
  const hasErrors = (i: number) => Object.keys(scoped(errs, i)).length > 0;

  function addPlan() {
    const free = WEEKDAYS.filter((d) => covered[d].length === 0);
    const created = newPlan(
      plans,
      plan.underlying ?? "NIFTY",
      inst?.weekly_expiry ?? true,
      free.length ? [...free] : [...WEEKDAYS],
    );
    withPlans([...plans, created]);
    setCurrent(plans.length);
  }

  function duplicatePlan() {
    const copy: TradePlan = { ...structuredClone(plan), id: nextPlanId(plans), name: `${plan.name} copy` };
    withPlans([...plans.slice(0, at + 1), copy, ...plans.slice(at + 1)]);
    setCurrent(at + 1);
  }

  function removePlan() {
    withPlans(plans.filter((_, j) => j !== at));
    setCurrent(Math.max(0, at - 1));
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="font-semibold">Plans</h2>
            <p className="text-sm text-muted">
              A plan is one complete routine for some weekdays: how it enters, what it trades, how it exits.
              Give each day the plan you want; plans may overlap and then run side by side.
            </p>
          </div>
          <Button size="sm" variant="secondary" disabled={plans.length >= MAX_PLANS} onClick={addPlan}>
            <Plus className="size-4" aria-hidden /> Add plan
          </Button>
        </div>
        <div role="tablist" aria-label="Plans" className="flex flex-wrap gap-1.5">
          {plans.map((p, i) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={i === at}
              onClick={() => setCurrent(i)}
              className={cn(
                "flex flex-col rounded-lg border px-3 py-1.5 text-left text-xs",
                i === at ? "border-primary bg-primary/10" : "border-border hover:bg-surface-2",
                hasErrors(i) && "border-loss",
              )}
            >
              <span className="font-semibold">{p.name}</span>
              <span className="text-muted">
                {(p.days ?? WEEKDAYS).map(short).join(" ")} · {p.underlying}
              </span>
            </button>
          ))}
        </div>
        <div className="grid grid-cols-5 gap-1.5 text-center text-xs" aria-label="Week at a glance">
          {WEEKDAYS.map((d) => (
            <div key={d} className="rounded-lg border border-border px-1 py-1.5">
              <div className="font-semibold">{short(d)}</div>
              <div className={cn("truncate", covered[d].length ? "text-muted" : "text-warning")}>
                {covered[d].length ? covered[d].join(" + ") : "no trade"}
              </div>
            </div>
          ))}
        </div>
        {errs["plans"] && <p className="text-sm text-loss">{errs["plans"]}</p>}
      </Card>

      <Card className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Plan name" error={planErrs["name"]}>
            {(a) => (
              <input
                {...a}
                className={inputClass}
                value={plan.name}
                maxLength={60}
                onChange={(e) => setPlan({ ...plan, name: e.target.value })}
              />
            )}
          </Field>
          <SelectField
            label="Instrument"
            value={plan.underlying ?? "NIFTY"}
            error={planErrs["underlying"]}
            options={catalog.instruments.map((i) => ({ value: i.code, label: `${i.code} · ${i.name}` }))}
            onChange={(underlying) =>
              setPlan({
                ...plan,
                underlying: underlying as TradePlan["underlying"],
                legs: plan.legs.map((l) => ({
                  ...l,
                  expiry:
                    catalog.instruments.find((i) => i.code === underlying)?.weekly_expiry === false &&
                    l.expiry.endsWith("week")
                      ? "current_month"
                      : l.expiry,
                })),
              })
            }
          />
          <div className="flex items-end justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={duplicatePlan}
              disabled={plans.length >= MAX_PLANS}
            >
              <Copy className="size-4" aria-hidden /> Duplicate plan
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={removePlan}
              disabled={plans.length <= 1}
              aria-label="Remove plan"
            >
              <Trash2 className="size-4" aria-hidden /> Remove
            </Button>
          </div>
        </div>
        <Chips<Weekday>
          id={`days-${plan.id}`}
          label="Trade this plan on"
          values={WEEKDAYS}
          selected={(plan.days ?? [...WEEKDAYS]) as Weekday[]}
          render={short}
          error={planErrs["days"] ?? planErrs["entry.days"]}
          onToggle={(d, on) => {
            const cur = (plan.days ?? [...WEEKDAYS]) as Weekday[];
            setPlan({
              ...plan,
              days: on ? WEEKDAYS.filter((x) => x === d || cur.includes(x)) : cur.filter((x) => x !== d),
            });
          }}
        />
        {inst && (
          <p className="text-xs text-muted">
            Lot size {inst.lot_size} · strikes every {inst.strike_step} ·{" "}
            {inst.weekly_expiry ? "weekly" : "monthly"} expiries · trades {inst.session_open}–
            {inst.session_close}
          </p>
        )}
      </Card>

      <PlanEditor
        key={plan.id}
        plan={plan}
        catalog={catalog}
        inst={inst}
        hours={hours}
        errs={planErrs}
        warns={planWarns}
        onChange={setPlan}
      />
    </div>
  );
}
