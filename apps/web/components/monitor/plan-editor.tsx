"use client";

import type { FeatureInfo, PlanAdmin } from "@algoearning/api-types";
import { Button, Card, StatusPill, Switch, cn } from "@algoearning/ui";
import { useState } from "react";

import { Field, inputClass } from "@/components/builder/fields";

import { rupees } from "./bits";
import { FeatureEditor } from "./feature-editor";
import { useAdminAction } from "./use-admin-action";

export function PlanEditor({ plan, catalog }: { plan: PlanAdmin; catalog: FeatureInfo[] }) {
  const { run, busy, note } = useAdminAction();
  const [name, setName] = useState(plan.name);
  const [rupeesValue, setRupees] = useState(plan.price_paise / 100);
  const [active, setActive] = useState(plan.is_active);
  const [sort, setSort] = useState(plan.sort_order);
  const [features, setFeatures] = useState(plan.features as Record<string, boolean | number | null>);
  const [open, setOpen] = useState(false);

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">
            {plan.name} <span className="text-sm font-normal text-muted">· {plan.code}</span>
          </h2>
          <p className="text-sm text-muted">
            {rupees(plan.price_paise)} per {plan.interval}
          </p>
        </div>
        <StatusPill tone={plan.is_active ? "success" : "neutral"}>
          {plan.is_active ? "on sale" : "not on sale"}
        </StatusPill>
        <Button size="sm" variant="secondary" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Close" : "Edit"}
        </Button>
      </div>
      {open && (
        <form
          className="flex flex-col gap-4 border-t border-border pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              "save",
              "PATCH",
              `/v1/admin/plans/${plan.code}`,
              {
                name,
                price_paise: Math.round(rupeesValue * 100),
                is_active: active,
                sort_order: sort,
                features,
              },
              "Saved. Every user on this plan gets the new limits now.",
            );
          }}
        >
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Name">
              {(a) => (
                <input {...a} className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
              )}
            </Field>
            <Field label={`Price (₹ per ${plan.interval})`}>
              {(a) => (
                <input
                  {...a}
                  type="number"
                  min={0}
                  step={1}
                  className={inputClass}
                  value={rupeesValue}
                  onChange={(e) => setRupees(Math.max(0, e.target.valueAsNumber || 0))}
                />
              )}
            </Field>
            <Field label="Order on the pricing page">
              {(a) => (
                <input
                  {...a}
                  type="number"
                  className={inputClass}
                  value={sort}
                  onChange={(e) => setSort(Math.trunc(e.target.valueAsNumber || 0))}
                />
              )}
            </Field>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted">
              On sale
              <Switch
                checked={active}
                onCheckedChange={setActive}
                disabled={plan.code === "free"}
                aria-label="On sale"
              />
            </label>
          </div>
          <FeatureEditor catalog={catalog} value={features} onChange={setFeatures} mode="plan" />
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={busy !== null}>
              Save plan
            </Button>
            {note && (
              <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
                {note.text}
              </p>
            )}
          </div>
        </form>
      )}
    </Card>
  );
}
