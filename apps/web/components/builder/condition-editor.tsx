"use client";

import type { Condition, ConditionGroup, ConditionOperand } from "@algoearning/api-types";
import { Button } from "@algoearning/ui";
import { Plus, Trash2 } from "lucide-react";

import { LEVEL_LABEL, OP_LABEL } from "@/lib/strategy";

import { NumberField, SelectField } from "./fields";

type Errs = Record<string, string>;
type Level = NonNullable<ConditionOperand["level"]>;

const CANDLES = [1, 3, 5, 15, 30, 60] as const;
const PRICE: ConditionOperand = { kind: "price", minutes: 15 };
const LEVEL: ConditionOperand = { kind: "level", level: "opening_high", minutes: 15 };

/** What a value is: the candle's price, a ready-made level, or a number. */
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
        ]}
        onChange={(v) =>
          onChange(
            v === "price"
              ? PRICE
              : v === "number"
                ? { kind: "number", minutes: 15, value: value.value ?? 25000 }
                : { ...LEVEL, level: v as Level, minutes: value.minutes ?? 15 },
          )
        }
      />
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
