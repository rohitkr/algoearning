"use client";

import type {
  Instrument,
  LegReEntry,
  LegStrike,
  LegThreshold,
  LegTrailing,
  StrategyLeg,
} from "@algoearning/api-types";
import { Button, cn } from "@algoearning/ui";
import { Copy, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { EXPIRY_LABEL, legQuantity } from "@/lib/strategy";

import { Check, NumberField, Segmented, SelectField } from "./fields";

type Errs = Record<string, string>;

const STRIKE_CHOICES = (max: number) => [
  ...Array.from({ length: max }, (_, i) => max - i).map((n) => ({ value: String(-n), label: `ITM ${n}` })),
  { value: "0", label: "ATM" },
  ...Array.from({ length: max }, (_, i) => i + 1).map((n) => ({ value: String(n), label: `OTM ${n}` })),
  { value: "premium", label: "Premium near" },
  { value: "premium_gte", label: "Premium at least" },
  { value: "premium_lte", label: "Premium at most" },
  { value: "points", label: "Points from index" },
];
const BY_PRICE = new Set(["premium", "premium_gte", "premium_lte"]);

/** A toggleable block of options (stop-loss, target, ...): off = null in the config. */
function Optional({
  label,
  on,
  onToggle,
  error,
  children,
}: {
  label: string;
  on: boolean;
  onToggle: (on: boolean) => void;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("rounded-lg border border-border p-3", on ? "bg-surface" : "bg-surface-2/50")}>
      <Check label={label} checked={on} onChange={onToggle} error={error} />
      {on && <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{children}</div>}
    </div>
  );
}

function ThresholdFields({
  value,
  onChange,
  errs,
  path,
}: {
  value: LegThreshold;
  onChange: (t: LegThreshold) => void;
  errs: Errs;
  path: string;
}) {
  return (
    <>
      <NumberField
        label="Value"
        value={value.value}
        step={0.5}
        min={0}
        suffix={value.unit === "points" ? "pts" : "%"}
        error={errs[`${path}.value`]}
        onChange={(v) => onChange({ ...value, value: v ?? Number.NaN })}
      />
      <SelectField
        label="In"
        value={value.unit ?? "percent"}
        options={[
          { value: "percent", label: "Percent" },
          { value: "points", label: "Points" },
        ]}
        onChange={(unit) => onChange({ ...value, unit })}
      />
      <SelectField
        label="Measured on"
        value={value.basis ?? "premium"}
        options={[
          { value: "premium", label: "Option premium" },
          { value: "underlying", label: "Index" },
        ]}
        onChange={(basis) => onChange({ ...value, basis })}
      />
    </>
  );
}

function ReEntryFields({ value, onChange }: { value: LegReEntry; onChange: (r: LegReEntry) => void }) {
  return (
    <>
      <SelectField
        label="When"
        value={value.mode ?? "at_cost"}
        options={[
          { value: "at_cost", label: "Back at entry price" },
          { value: "immediate", label: "Immediately" },
        ]}
        onChange={(mode) => onChange({ ...value, mode })}
      />
      <NumberField
        label="Times"
        value={value.count ?? 1}
        min={1}
        max={5}
        onChange={(v) => onChange({ ...value, count: v ?? Number.NaN })}
      />
    </>
  );
}

export function LegEditor({
  leg,
  index,
  instrument,
  maxOffset,
  errs,
  warns,
  canRemove,
  onChange,
  onDuplicate,
  onRemove,
}: {
  leg: StrategyLeg;
  index: number;
  instrument: Instrument | undefined;
  maxOffset: number;
  errs: Errs;
  warns: Errs;
  canRemove: boolean;
  onChange: (l: StrategyLeg) => void;
  onDuplicate?: () => void;
  onRemove: () => void;
}) {
  const p = `legs.${index}`;
  const set = <K extends keyof StrategyLeg>(k: K, v: StrategyLeg[K]) => onChange({ ...leg, [k]: v });
  const strike: LegStrike = leg.strike ?? { mode: "atm", offset: 0, premium: null, points: null };
  const qty = legQuantity(leg, instrument);
  const weekly = instrument?.weekly_expiry ?? true;
  const sl: LegThreshold = { unit: "percent", value: 30, basis: "premium" };
  const trail: LegTrailing = { unit: "points", trigger: 10, step: 5 };
  const re: LegReEntry = { mode: "at_cost", count: 1 };

  return (
    <fieldset className="rounded-xl border border-border bg-surface p-4">
      <legend className="sr-only">Leg {index + 1}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm font-semibold" aria-hidden>
          Leg {index + 1}
        </span>
        <Segmented
          label={`Leg ${index + 1} action`}
          value={leg.action}
          options={[
            { value: "BUY", label: "BUY" },
            { value: "SELL", label: "SELL" },
          ]}
          tones={{ BUY: "bg-profit text-white", SELL: "bg-loss text-white" }}
          onChange={(v) => set("action", v)}
        />
        <Segmented
          label={`Leg ${index + 1} option type`}
          value={leg.option_type}
          options={[
            { value: "CE", label: "CE" },
            { value: "PE", label: "PE" },
          ]}
          onChange={(v) => set("option_type", v)}
        />
        <span className="ml-auto flex gap-1">
          {onDuplicate && (
            <Button size="icon" variant="ghost" onClick={onDuplicate} aria-label={`Copy leg ${index + 1}`}>
              <Copy className="size-4" aria-hidden />
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            onClick={onRemove}
            disabled={!canRemove}
            aria-label={`Remove leg ${index + 1}`}
          >
            <Trash2 className="size-4" aria-hidden />
          </Button>
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <NumberField
          label="Lots"
          value={leg.lots}
          min={1}
          error={errs[`${p}.lots`]}
          hint={qty ? `${qty} qty` : undefined}
          onChange={(v) => set("lots", v ?? Number.NaN)}
        />
        <SelectField
          label="Expiry"
          value={leg.expiry}
          error={errs[`${p}.expiry`]}
          options={(Object.keys(EXPIRY_LABEL) as StrategyLeg["expiry"][]).map((e) => ({
            value: e,
            label: EXPIRY_LABEL[e],
            disabled: !weekly && e.endsWith("week"),
          }))}
          onChange={(v) => set("expiry", v)}
        />
        <SelectField
          label="Strike"
          value={strike.mode === "atm" ? String(strike.offset ?? 0) : strike.mode}
          options={STRIKE_CHOICES(maxOffset)}
          error={errs[`${p}.strike.offset`] ?? errs[`${p}.strike.mode`]}
          onChange={(v) =>
            set(
              "strike",
              BY_PRICE.has(v)
                ? { mode: v as LegStrike["mode"], offset: 0, premium: strike.premium ?? 100, points: null }
                : v === "points"
                  ? { mode: "points", offset: 0, premium: null, points: strike.points ?? 200 }
                  : { mode: "atm", offset: Number(v), premium: null, points: null },
            )
          }
        />
        {BY_PRICE.has(strike.mode) ? (
          <NumberField
            label="Premium"
            value={strike.premium}
            min={0}
            suffix="₹"
            error={errs[`${p}.strike.premium`]}
            onChange={(v) => set("strike", { ...strike, premium: v })}
          />
        ) : strike.mode === "points" ? (
          <NumberField
            label="Points"
            value={strike.points}
            step={50}
            suffix="pts"
            hint="+ out of the money, − in the money"
            error={errs[`${p}.strike.points`]}
            onChange={(v) => set("strike", { ...strike, points: v })}
          />
        ) : (
          <div className="hidden sm:block" />
        )}
      </div>
      {warns[`${p}.lots`] && <p className="mt-2 text-xs text-warning">{warns[`${p}.lots`]}</p>}

      <div className="mt-4 grid gap-2 lg:grid-cols-2">
        <Optional
          label="Stop-loss"
          on={!!leg.stop_loss}
          onToggle={(on) => set("stop_loss", on ? sl : null)}
          error={errs[`${p}.stop_loss`]}
        >
          {leg.stop_loss && (
            <ThresholdFields
              value={leg.stop_loss}
              errs={errs}
              path={`${p}.stop_loss`}
              onChange={(t) => set("stop_loss", t)}
            />
          )}
        </Optional>
        <Optional
          label="Target"
          on={!!leg.target}
          onToggle={(on) => set("target", on ? { ...sl, value: 50 } : null)}
          error={errs[`${p}.target`]}
        >
          {leg.target && (
            <ThresholdFields
              value={leg.target}
              errs={errs}
              path={`${p}.target`}
              onChange={(t) => set("target", t)}
            />
          )}
        </Optional>
        <Optional
          label="Trailing stop-loss"
          on={!!leg.trailing}
          onToggle={(on) => set("trailing", on ? trail : null)}
          error={errs[`${p}.trailing`]}
        >
          {leg.trailing && (
            <>
              <NumberField
                label="Every move of"
                value={leg.trailing.trigger}
                min={0}
                error={errs[`${p}.trailing.trigger`]}
                onChange={(v) =>
                  leg.trailing && set("trailing", { ...leg.trailing, trigger: v ?? Number.NaN })
                }
              />
              <NumberField
                label="Move SL by"
                value={leg.trailing.step}
                min={0}
                error={errs[`${p}.trailing.step`]}
                onChange={(v) => leg.trailing && set("trailing", { ...leg.trailing, step: v ?? Number.NaN })}
              />
              <SelectField
                label="In"
                value={leg.trailing.unit ?? "points"}
                options={[
                  { value: "points", label: "Points" },
                  { value: "percent", label: "Percent" },
                ]}
                onChange={(unit) => leg.trailing && set("trailing", { ...leg.trailing, unit })}
              />
            </>
          )}
        </Optional>
        <Optional
          label="Re-enter after stop-loss"
          on={!!leg.reentry_on_sl}
          onToggle={(on) => set("reentry_on_sl", on ? re : null)}
          error={errs[`${p}.reentry_on_sl`]}
        >
          {leg.reentry_on_sl && (
            <ReEntryFields value={leg.reentry_on_sl} onChange={(r) => set("reentry_on_sl", r)} />
          )}
        </Optional>
        <Optional
          label="Re-enter after target"
          on={!!leg.reentry_on_target}
          onToggle={(on) => set("reentry_on_target", on ? re : null)}
          error={errs[`${p}.reentry_on_target`]}
        >
          {leg.reentry_on_target && (
            <ReEntryFields value={leg.reentry_on_target} onChange={(r) => set("reentry_on_target", r)} />
          )}
        </Optional>
      </div>
    </fieldset>
  );
}
