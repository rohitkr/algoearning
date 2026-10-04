"use client";

import type { Condition, ConditionGroup, ConditionOperand } from "@algoearning/api-types";
import { Button } from "@algoearning/ui";
import { Plus, Trash2 } from "lucide-react";

import {
  INDICATOR_LABEL,
  INDICATOR_LINES,
  type IndicatorName,
  LEVEL_LABEL,
  LINE_LABEL,
  OP_LABEL,
  indicatorOperand,
  operand,
} from "@/lib/strategy";

import { NumberField, SelectField } from "./fields";

type Errs = Record<string, string>;
type Level = NonNullable<ConditionOperand["level"]>;

const CANDLES = [1, 3, 5, 15, 30, 60] as const;
const PRICE: ConditionOperand = operand({ kind: "price" });
const LEVEL: ConditionOperand = operand({ kind: "level", level: "opening_high" });

/** An indicator's settings: which one, its period, which of its lines, and its extra parameters. */
function IndicatorFields({
  value,
  onChange,
  errs,
  path,
}: {
  value: ConditionOperand;
  onChange: (o: ConditionOperand) => void;
  errs: Errs;
  path: string;
}) {
  const name = (value.indicator ?? "ema") as IndicatorName;
  const lines = INDICATOR_LINES[name];
  return (
    <>
      <SelectField
        label="Indicator"
        value={name}
        error={errs[`${path}.indicator`]}
        options={(Object.keys(INDICATOR_LABEL) as IndicatorName[]).map((n) => ({
          value: n,
          label: INDICATOR_LABEL[n],
        }))}
        onChange={(n) => onChange(indicatorOperand(n))}
      />
      <NumberField
        label={name === "macd" ? "Slow period" : "Period"}
        value={value.period}
        min={1}
        max={200}
        error={errs[`${path}.period`]}
        onChange={(v) => onChange({ ...value, period: v ?? Number.NaN })}
      />
      {lines && (
        <SelectField
          label="Output"
          value={value.line}
          error={errs[`${path}.line`]}
          options={lines.map((l) => ({ value: l, label: LINE_LABEL[l] }))}
          onChange={(line) => onChange({ ...value, line })}
        />
      )}
      {(name === "supertrend" || name === "bollinger") && (
        <NumberField
          label={name === "supertrend" ? "ATR multiplier" : "Band width (σ)"}
          value={value.multiplier}
          step={0.5}
          min={0}
          max={10}
          error={errs[`${path}.multiplier`]}
          onChange={(v) => onChange({ ...value, multiplier: v })}
        />
      )}
      {name === "macd" && (
        <>
          <NumberField
            label="Fast period"
            value={value.fast}
            min={1}
            max={100}
            error={errs[`${path}.fast`]}
            onChange={(v) => onChange({ ...value, fast: v ?? Number.NaN })}
          />
          <NumberField
            label="Signal period"
            value={value.smoothing}
            min={1}
            max={100}
            error={errs[`${path}.smoothing`]}
            onChange={(v) => onChange({ ...value, smoothing: v ?? Number.NaN })}
          />
        </>
      )}
    </>
  );
}

/** What a value is: the candle's price, a ready-made level, a number, or an indicator. */
function OperandFields({
  label,
  value,
  onChange,
  errs,
  path,
  allowPrice,
}: {
  label: string;
  value: ConditionOperand;
  onChange: (o: ConditionOperand) => void;
  errs: Errs;
  path: string;
  allowPrice: boolean;
}) {
  const choice = value.kind === "level" ? (value.level ?? "opening_high") : value.kind;
  return (
    <>
      <SelectField
        label={label}
        value={choice as string}
        error={errs[`${path}.level`]}
        options={[
          ...(allowPrice ? [{ value: "price", label: "Price (candle close)" }] : []),
          ...(Object.keys(LEVEL_LABEL) as Level[]).map((l) => ({ value: l, label: LEVEL_LABEL[l] })),
          { value: "number", label: "A number" },
          { value: "indicator", label: "An indicator" },
        ]}
        onChange={(v) =>
          onChange(
            v === "price"
              ? PRICE
              : v === "number"
                ? operand({ kind: "number", value: value.value ?? 25000 })
                : v === "indicator"
                  ? value.kind === "indicator"
                    ? value
                    : indicatorOperand("ema")
                  : { ...LEVEL, level: v as Level, minutes: value.minutes ?? 15 },
          )
        }
      />
      {value.kind === "indicator" && (
        <IndicatorFields value={value} onChange={onChange} errs={errs} path={path} />
      )}
      {value.kind === "level" && value.level?.startsWith("opening_") && (
        <NumberField
          label="First … minutes"
          value={value.minutes}
          min={1}
          max={180}
          error={errs[`${path}.minutes`]}
          onChange={(v) => onChange({ ...value, minutes: v ?? Number.NaN })}
        />
      )}
      {value.kind === "number" && (
        <NumberField
          label="Number"
          value={value.value}
          step={1}
          error={errs[`${path}.value`]}
          onChange={(v) => onChange({ ...value, value: v })}
        />
      )}
    </>
  );
}

const blank = (): Condition => ({ left: PRICE, op: "crosses_above", right: LEVEL, candle: 5 });

/** Rows of "[value] [comparison] [value] on N-minute candles", combined with ALL / ANY. */
export function ConditionGroupEditor({
  group,
  onChange,
  errs,
  path,
}: {
  group: ConditionGroup;
  onChange: (g: ConditionGroup) => void;
  errs: Errs;
  path: string;
}) {
  const set = (i: number, c: Condition) =>
    onChange({ ...group, conditions: group.conditions.map((x, j) => (j === i ? c : x)) });
  return (
    <div className="flex flex-col gap-3">
      {group.conditions.length > 1 && (
        <SelectField
          label="The signal fires when"
          value={group.match}
          options={[
            { value: "all", label: "All of these are true" },
            { value: "any", label: "Any of these is true" },
          ]}
          onChange={(match) => onChange({ ...group, match })}
        />
      )}
      {group.conditions.map((c, i) => {
        const p = `${path}.conditions.${i}`;
        return (
          <div key={i} className="rounded-lg border border-border bg-surface-2/50 p-3">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <OperandFields
                label="When"
                value={c.left ?? PRICE}
                allowPrice
                errs={errs}
                path={`${p}.left`}
                onChange={(left) => set(i, { ...c, left })}
              />
              <SelectField
                label="Comparison"
                value={c.op}
                options={(Object.keys(OP_LABEL) as Condition["op"][]).map((o) => ({
                  value: o,
                  label: OP_LABEL[o],
                }))}
                onChange={(op) => set(i, { ...c, op })}
              />
              <OperandFields
                label="Compared with"
                value={c.right ?? LEVEL}
                allowPrice
                errs={errs}
                path={`${p}.right`}
                onChange={(right) => set(i, { ...c, right })}
              />
              <SelectField
                label="Candle"
                value={String(c.candle)}
                options={CANDLES.map((n) => ({ value: String(n), label: `${n} min` }))}
                onChange={(v) => set(i, { ...c, candle: Number(v) as Condition["candle"] })}
              />
            </div>
            {errs[p] && <p className="mt-2 text-xs text-loss">{errs[p]}</p>}
            {group.conditions.length > 1 && (
              <Button
                size="sm"
                variant="ghost"
                className="mt-2"
                onClick={() => onChange({ ...group, conditions: group.conditions.filter((_, j) => j !== i) })}
              >
                <Trash2 className="size-4" aria-hidden /> Remove condition
              </Button>
            )}
          </div>
        );
      })}
      {group.conditions.length < 6 && (
        <Button
          size="sm"
          variant="secondary"
          className="self-start"
          onClick={() => onChange({ ...group, conditions: [...group.conditions, blank()] })}
        >
          <Plus className="size-4" aria-hidden /> Add condition
        </Button>
      )}
    </div>
  );
}

export { blank as blankCondition };
