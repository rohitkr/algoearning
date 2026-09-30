"use client";

import type { FeatureInfo } from "@algoearning/api-types";

import { inputClass } from "@/components/builder/fields";

type Value = boolean | number | null;

/** Edit feature values. mode "plan": every feature has a value. mode "override": a feature may be left to the
 * plan (absent from the map), shown with the plan's value as a hint. */
export function FeatureEditor({
  catalog,
  value,
  onChange,
  mode,
  planValues,
}: {
  catalog: FeatureInfo[];
  value: Record<string, Value>;
  onChange: (v: Record<string, Value>) => void;
  mode: "plan" | "override";
  planValues?: Record<string, Value | undefined>;
}) {
  const set = (key: string, v: Value | undefined) => {
    const next = { ...value };
    if (v === undefined) delete next[key];
    else next[key] = v;
    onChange(next);
  };
  const show = (v: Value | undefined) =>
    v === true ? "On" : v === false ? "Off" : v == null ? "Unlimited" : String(v);
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {catalog.map((f) => {
        const has = f.key in value;
        const v = value[f.key];
        const id = `feat-${mode}-${f.key}`;
        const choice = !has
          ? "plan"
          : f.kind === "flag"
            ? v
              ? "on"
              : "off"
            : v == null
              ? "unlimited"
              : "number";
        return (
          <div key={f.key} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-xs font-medium text-muted">
              {f.label}
              {mode === "override" && planValues && (
                <span className="font-normal"> · plan: {show(planValues[f.key])}</span>
              )}
            </label>
            <div className="flex gap-2">
              <select
                id={id}
                className={inputClass}
                value={choice}
                onChange={(e) => {
                  const c = e.target.value;
                  if (c === "plan") set(f.key, undefined);
                  else if (c === "on") set(f.key, true);
                  else if (c === "off") set(f.key, false);
                  else if (c === "unlimited") set(f.key, null);
                  else
                    set(
                      f.key,
                      typeof v === "number"
                        ? v
                        : typeof planValues?.[f.key] === "number"
                          ? (planValues[f.key] as number)
                          : 1,
                    );
                }}
              >
                {mode === "override" && <option value="plan">Use the plan</option>}
                {f.kind === "flag" ? (
                  <>
                    <option value="on">On</option>
                    <option value="off">Off</option>
                  </>
                ) : (
                  <>
                    <option value="number">Limit</option>
                    <option value="unlimited">Unlimited</option>
                  </>
                )}
              </select>
              {choice === "number" && (
                <input
                  type="number"
                  min={0}
                  aria-label={`${f.label} limit`}
                  className={`${inputClass} w-24`}
                  value={typeof v === "number" ? v : ""}
                  onChange={(e) =>
                    set(f.key, e.target.value === "" ? 0 : Math.max(0, Math.trunc(e.target.valueAsNumber)))
                  }
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
