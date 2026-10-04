"use client";

import type { RulesCondition, RulesConditionGroup, RulesOperand } from "@algoearning/api-types";
import { Button, cn } from "@algoearning/ui";
import { Plus, X } from "lucide-react";

import { LEVEL_LABEL, OP_LABEL, TIMEFRAMES, newCondition } from "@/lib/strategy";

import { NumberField, SelectField, TimeField } from "./fields";

type Errs = Record<string, string>;
type Level = Extract<RulesOperand, { kind: "level" }>;

const MAX_CONDITIONS = 6;

const KIND_LABEL: Record<RulesOperand["kind"], string> = {
  price: "Index candle",
  level: "Level",
  number: "Number",
};

function blank(kind: RulesOperand["kind"]): RulesOperand {
  if (kind === "price") return { kind: "price", field: "close" };
  if (kind === "number") return { kind: "number", value: 0 };
  return { kind: "level", name: "opening_range_high", minutes: 15, at: null, offset: 0 };
}

/** One side of a condition: the index candle's OHLC, a level of the day (with an offset), or a number. */
function OperandEditor({
  label,
  value,
  path,
  errs,
  hours,
  onChange,
}: {
  label: string;
  value: RulesOperand;
  path: string;
  errs: Errs;
  hours: { min?: string; max?: string };
  onChange: (o: RulesOperand) => void;
}) {
  const lv = value.kind === "level" ? value : null;
  const setLevel = (p: Partial<Level>) => lv && onChange({ ...lv, ...p });
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface-2/40 p-2">
      <SelectField
        label={label}
        value={value.kind}
        error={errs[path]}
        options={(Object.keys(KIND_LABEL) as RulesOperand["kind"][]).map((k) => ({
          value: k,
          label: KIND_LABEL[k],
        }))}
        onChange={(k) => onChange(blank(k))}
      />
      {value.kind === "price" && (
        <SelectField
          label="Value"
          value={value.field ?? "close"}
          options={(["close", "open", "high", "low"] as const).map((f) => ({
            value: f,
            label: f[0]!.toUpperCase() + f.slice(1),
          }))}
          onChange={(field) => onChange({ ...value, field })}
        />
      )}
      {value.kind === "number" && (
        <NumberField
          label="Value"
          value={value.value}
          step={0.5}
          error={errs[`${path}.value`]}
          onChange={(v) => onChange({ ...value, value: v ?? Number.NaN })}
        />
      )}
      {lv && (
        <>
          <SelectField
            label="Which"
            value={lv.name}
            options={(Object.keys(LEVEL_LABEL) as Level["name"][]).map((n) => ({
              value: n,
              label: LEVEL_LABEL[n],
            }))}
            onChange={(name) =>
              setLevel({
                name,
                at: name === "price_at" ? (lv.at ?? "09:20") : null,
                minutes: lv.minutes ?? 15,
              })
            }
          />
          <div className="grid grid-cols-2 gap-2">
            {lv.name.startsWith("opening_range") && (
              <NumberField
                label="First minutes"
                value={lv.minutes}
                min={1}
                max={180}
                error={errs[`${path}.minutes`]}
                onChange={(v) => setLevel({ minutes: v ?? Number.NaN })}
              />
            )}
            {lv.name === "price_at" && (
              <TimeField
                label="At"
                value={lv.at ?? "09:20"}
                {...hours}
                error={errs[`${path}.at`]}
                onChange={(at) => setLevel({ at })}
              />
            )}
            <NumberField
              label="Offset"
              value={lv.offset}
              step={5}
              suffix="pts"
              hint="+ above, − below"
              error={errs[`${path}.offset`]}
              onChange={(v) => setLevel({ offset: v ?? 0 })}
            />
          </div>
        </>
      )}
    </div>
  );
}

function ConditionRow({
  cond,
  path,
  errs,
  hours,
  canRemove,
  onChange,
  onRemove,
}: {
  cond: RulesCondition;
  path: string;
  errs: Errs;
  hours: { min?: string; max?: string };
  canRemove: boolean;
  onChange: (c: RulesCondition) => void;
  onRemove: () => void;
}) {
  const left = cond.left ?? { kind: "price", field: "close" };
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex items-end gap-2">
        <SelectField
          label="Candles"
          className="w-28"
          value={String(cond.timeframe ?? 5)}
          options={TIMEFRAMES.map((t) => ({ value: String(t), label: `${t} min` }))}
          onChange={(v) => onChange({ ...cond, timeframe: Number(v) as RulesCondition["timeframe"] })}
        />
        <SelectField
          label="Rule"
          className="flex-1"
          value={cond.op ?? "crosses_above"}
          options={(Object.keys(OP_LABEL) as RulesCondition["op"][]).map((op) => ({
            value: op,
            label: OP_LABEL[op],
          }))}
          onChange={(op) => onChange({ ...cond, op })}
        />
        {canRemove && (
          <Button size="sm" variant="ghost" aria-label="Remove condition" onClick={onRemove}>
            <X className="size-4" aria-hidden />
          </Button>
        )}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <OperandEditor
          label="This"
          value={left}
          path={`${path}.left`}
          errs={errs}
          hours={hours}
          onChange={(o) => onChange({ ...cond, left: o })}
        />
        <OperandEditor
          label={OP_LABEL[cond.op ?? "crosses_above"].replace(/^(is )?/, "") + " this"}
          value={cond.right}
          path={`${path}.right`}
          errs={errs}
          hours={hours}
          onChange={(o) => onChange({ ...cond, right: o })}
        />
      </div>
    </div>
  );
}

/** A group of conditions (all / any), read on the index candles when each candle completes (ADR 0022). */
export function ConditionGroupEditor({
  title,
  hint,
  group,
  path,
  errs,
  hours,
  onChange,
}: {
  title: string;
  hint?: string;
  group: RulesConditionGroup;
  path: string;
  errs: Errs;
  hours: { min?: string; max?: string };
  onChange: (g: RulesConditionGroup) => void;
}) {
  const conds = group.conditions;
  return (
    <div className={cn("flex flex-col gap-3 rounded-lg border border-border bg-surface p-3")}>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{title}</p>
          {hint && <p className="text-xs text-muted">{hint}</p>}
        </div>
        {conds.length > 1 && (
          <SelectField
            label="Needs"
            className="w-40"
            value={group.match ?? "all"}
            options={[
              { value: "all", label: "All of them" },
              { value: "any", label: "Any one" },
            ]}
            onChange={(match) => onChange({ ...group, match })}
          />
        )}
      </div>
      {errs[path] && <p className="text-xs text-loss">{errs[path]}</p>}
      {conds.map((c, i) => (
        <ConditionRow
          key={i}
          cond={c}
          path={`${path}.conditions.${i}`}
          errs={errs}
          hours={hours}
          canRemove={conds.length > 1}
          onChange={(next) => onChange({ ...group, conditions: conds.map((x, j) => (j === i ? next : x)) })}
          onRemove={() => onChange({ ...group, conditions: conds.filter((_, j) => j !== i) })}
        />
      ))}
      <Button
        size="sm"
        variant="secondary"
        className="w-fit"
        disabled={conds.length >= MAX_CONDITIONS}
        onClick={() => onChange({ ...group, conditions: [...conds, newCondition()] })}
      >
        <Plus className="size-4" aria-hidden /> Add condition
      </Button>
    </div>
  );
}
