"use client";

import type { SmcScalpConfig } from "@algoearning/api-types";
import { Card } from "@algoearning/ui";
import { ChevronDown } from "lucide-react";

import { Check, NumberField, Segmented, SelectField, TimeField } from "./fields";

type Errs = Record<string, string>;
type Sections = Required<Omit<SmcScalpConfig, "kind" | "underlying">>;
type SectionKey = keyof Sections;

/** One field of a section: how to edit it and what it means (the API's rules are in ae_core.trading.smc). */
type Spec = {
  key: string;
  label: string;
  type: "int" | "float" | "optional_float" | "bool" | "time";
  suffix?: string;
  hint?: string;
  step?: number;
};

const RULES: Spec[] = [
  { key: "swing_bias", label: "Swing length, bias", type: "int", suffix: "candles",
    hint: "A swing high is above this many candles on each side" },
  { key: "swing_setup", label: "Swing length, setup", type: "int", suffix: "candles" },
  { key: "swing_entry", label: "Swing length, entry", type: "int", suffix: "candles" },
  { key: "bias_min_breaks", label: "Breaks for a clear bias", type: "int",
    hint: "Consecutive BOS/CHoCH in one direction on the bias timeframe; fewer = unclear, no trade" },
  { key: "displacement_atr", label: "Displacement body", type: "float", suffix: "× ATR", step: 0.1,
    hint: "A setup candle's body this big, closing in its outer quarter" },
  { key: "atr_period", label: "ATR period", type: "int", suffix: "candles" },
  { key: "fvg_min_atr", label: "Smallest FVG", type: "float", suffix: "× ATR", step: 0.05 },
  { key: "sweep_min_pct", label: "Sweep depth", type: "float", suffix: "%", step: 0.01,
    hint: "How far beyond the liquidity the wick must reach" },
  { key: "sweep_reclaim", label: "Close back inside within", type: "int", suffix: "candles" },
  { key: "sweep_lookback", label: "Sweep before the break within", type: "int", suffix: "candles" },
  { key: "equal_level_pct", label: "Equal highs/lows within", type: "float", suffix: "%", step: 0.01 },
  { key: "opening_range_minutes", label: "Opening range", type: "int", suffix: "min" },
  { key: "poi_max_age", label: "POI waits for a tap", type: "int", suffix: "candles" },
  { key: "confirm_bars", label: "Entry CHoCH within", type: "int", suffix: "candles", hint: "Entry candles after the tap" },
  { key: "min_room_r", label: "Room to opposing liquidity", type: "float", suffix: "R", step: 0.1,
    hint: "Skip when the next buy-side (longs) / sell-side (shorts) liquidity is closer" },
  { key: "premium_discount", label: "Longs only in discount, shorts only in premium", type: "bool" },
  { key: "internal_liquidity", label: "Entry-timeframe swings count as liquidity", type: "bool" },
  { key: "require_bias", label: "Trade only with a clear bias", type: "bool" },
  { key: "require_sweep", label: "Require a liquidity sweep before the break", type: "bool" },
  { key: "require_displacement", label: "Require displacement", type: "bool" },
  { key: "entry_confirm", label: "Wait for the entry CHoCH after the tap", type: "bool" },
]; // prettier-ignore

const RISK: Spec[] = [
  { key: "lots", label: "Lots", type: "int", hint: "Split over TP1 / TP2 / TP3 when tranches are on" },
  { key: "sl_buffer_pct", label: "Stop beyond the sweep by", type: "float", suffix: "%", step: 0.01 },
  { key: "min_risk_pct", label: "Tightest stop", type: "float", suffix: "%", step: 0.01,
    hint: "Of the index; tighter is noise for an option: no trade" },
  { key: "max_risk_pct", label: "Widest stop", type: "float", suffix: "%", step: 0.01 },
  { key: "premium_stop_pct", label: "Premium stop", type: "optional_float", suffix: "%",
    hint: "Also exit if the option falls this much. Empty = index stop only" },
  { key: "max_trades_per_day", label: "Trades per day", type: "int" },
  { key: "max_losses_per_day", label: "Losing trades per day", type: "int" },
  { key: "cooldown_minutes", label: "Pause after a loss", type: "int", suffix: "min" },
  { key: "tranches", label: "Split lots into TP1 / TP2 / TP3 tranches", type: "bool" },
  { key: "breakeven_at_tp1", label: "Stop to breakeven at TP1", type: "bool" },
  { key: "trail_at_tp2", label: "Stop to TP1 at TP2", type: "bool" },
]; // prettier-ignore

const LIQUIDITY: Spec[] = [
  { key: "min_volume", label: "Volume today at least", type: "int", suffix: "qty" },
  { key: "min_oi", label: "Open interest at least", type: "int", suffix: "qty" },
  { key: "max_spread_pct", label: "Bid/ask spread at most", type: "float", suffix: "%", step: 0.1 },
];

const SESSION: Spec[] = [
  { key: "start", label: "First entry", type: "time", hint: "After the opening range" },
  { key: "last_entry", label: "Last entry", type: "time" },
  { key: "exit", label: "Exit everything at", type: "time" },
];

function SpecFields({
  section,
  specs,
  values,
  defaults,
  hours,
  errs,
  warns,
  onChange,
}: {
  section: SectionKey;
  specs: Spec[];
  values: Record<string, unknown>;
  defaults: Record<string, unknown>;
  hours: { min?: string; max?: string };
  errs: Errs;
  warns: Errs;
  onChange: (key: string, v: unknown) => void;
}) {
  const val = (k: string) => (k in values ? values[k] : defaults[k]);
  const err = (k: string) => errs[`${section}.${k}`] ?? warns[`${section}.${k}`];
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {specs
          .filter((s) => s.type !== "bool")
          .map((s) =>
            s.type === "time" ? (
              <TimeField
                key={s.key}
                label={s.label}
                value={val(s.key) as string}
                {...hours}
                error={err(s.key)}
                onChange={(v) => onChange(s.key, v)}
              />
            ) : (
              <NumberField
                key={s.key}
                label={s.label}
                value={val(s.key) as number | null}
                suffix={s.suffix}
                step={s.step ?? (s.type === "int" ? 1 : 0.1)}
                optional={s.type === "optional_float"}
                error={err(s.key)}
                hint={s.hint}
                onChange={(v) => onChange(s.key, v)}
              />
            ),
          )}
      </div>
      {specs.some((s) => s.type === "bool") && (
        <div className="mt-4 flex flex-col gap-2">
          {specs
            .filter((s) => s.type === "bool")
            .map((s) => (
              <Check
                key={s.key}
                label={s.label}
                checked={!!val(s.key)}
                error={err(s.key)}
                onChange={(on) => onChange(s.key, on)}
              />
            ))}
        </div>
      )}
    </>
  );
}

/** The SMC options scalper's settings: timeframes, R:R and risk, option choice, hours, and the SMC rules. */
export function SmcParams({
  config,
  defaults,
  hours,
  errs,
  warns,
  onChange,
}: {
  config: SmcScalpConfig;
  defaults: SmcScalpConfig | undefined;
  hours: { min?: string; max?: string };
  errs: Errs;
  warns: Errs;
  onChange: (c: SmcScalpConfig) => void;
}) {
  const part = <K extends SectionKey>(k: K) =>
    ({ ...(defaults?.[k] ?? {}), ...(config[k] ?? {}) }) as Record<string, unknown>;
  const setPart = (k: SectionKey, key: string, v: unknown) =>
    onChange({ ...config, [k]: { ...part(k), [key]: v } });
  const tf = part("timeframes");
  const risk = part("risk");
  const opt = part("option");
  const strike = { mode: "atm", offset: 0, premium: null, ...(opt.strike as object) } as {
    mode: "atm" | "premium";
    offset: number;
    premium: number | null;
  };
  const common = { hours, errs, warns };

  return (
    <>
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-semibold">Timeframes</h2>
          <p className="text-sm text-muted">
            Bias from the higher timeframe, the setup on the middle one, the entry on the lowest. All are
            built from the index&apos;s 1-minute candles.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {(
            [
              ["bias", "Bias", [5, 10, 15, 30, 60]],
              ["setup", "Setup (SMC confirmation)", [2, 3, 5, 10, 15]],
              ["entry", "Entry", [1, 2, 3]],
            ] as const
          ).map(([k, label, choices]) => (
            <SelectField
              key={k}
              label={label}
              value={String(tf[k] ?? choices[0])}
              error={errs[`timeframes.${k}`]}
              options={choices.map((m) => ({ value: String(m), label: `${m} min` }))}
              onChange={(v) => setPart("timeframes", k, Number(v))}
            />
          ))}
        </div>
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-semibold">Targets and risk</h2>
          <p className="text-sm text-muted">
            The stop sits beyond the swept liquidity. TP1 is 1R, TP2 halfway, TP3 the R:R chosen here.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-medium text-muted">Risk : reward</span>
          <Segmented
            label="Risk to reward"
            value={String(risk.rr ?? 2) as "2" | "3" | "4"}
            options={[
              { value: "2", label: "1:2" },
              { value: "3", label: "1:3" },
              { value: "4", label: "1:4" },
            ]}
            onChange={(v) => setPart("risk", "rr", Number(v))}
          />
        </div>
        <SpecFields
          section="risk"
          specs={RISK}
          values={config.risk ?? {}}
          defaults={defaults?.risk ?? {}}
          {...common}
          onChange={(k, v) => setPart("risk", k, v)}
        />
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-semibold">Option to buy</h2>
          <p className="text-sm text-muted">
            A bullish signal buys a call, a bearish one a put. Illiquid strikes fall back one strike toward
            the money, else the signal is skipped.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Strike by</span>
            <Segmented
              label="Strike by"
              value={strike.mode}
              options={[
                { value: "atm", label: "ATM ± strikes" },
                { value: "premium", label: "Premium" },
              ]}
              onChange={(m) => setPart("option", "strike", { ...strike, mode: m })}
            />
          </div>
          {strike.mode === "atm" ? (
            <NumberField
              className="w-44"
              label="Strikes from ATM"
              value={strike.offset}
              hint="+ out of the money, − in the money"
              error={errs["option.strike.offset"]}
              onChange={(v) => setPart("option", "strike", { ...strike, offset: v ?? 0 })}
            />
          ) : (
            <NumberField
              className="w-44"
              label="Premium near"
              value={strike.premium}
              suffix="₹"
              error={errs["option.strike.premium"]}
              onChange={(v) => setPart("option", "strike", { ...strike, premium: v })}
            />
          )}
          <SelectField
            label="Expiry"
            value={(opt.expiry as "nearest" | "next") ?? "nearest"}
            error={errs["option.expiry"]}
            options={[
              { value: "nearest", label: "Nearest" },
              { value: "next", label: "The one after" },
            ]}
            onChange={(v) =>
              onChange({
                ...config,
                option: {
                  ...part("option"),
                  expiry: v,
                  expiry_day_cutoff: v === "next" ? null : "12:00",
                } as SmcScalpConfig["option"],
              })
            }
          />
        </div>
        {opt.expiry !== "next" && (
          <div className="flex flex-wrap items-end gap-3">
            <Check
              label="On expiry day, switch to the next expiry from"
              checked={opt.expiry_day_cutoff != null}
              onChange={(on) => setPart("option", "expiry_day_cutoff", on ? "12:00" : null)}
            />
            {opt.expiry_day_cutoff != null && (
              <TimeField
                label="Switch at"
                value={opt.expiry_day_cutoff as string}
                {...hours}
                error={errs["option.expiry_day_cutoff"]}
                onChange={(v) => setPart("option", "expiry_day_cutoff", v)}
              />
            )}
          </div>
        )}
        <SpecFields
          section="option"
          specs={LIQUIDITY}
          values={config.option ?? {}}
          defaults={defaults?.option ?? {}}
          {...common}
          onChange={(k, v) => setPart("option", k, v)}
        />
      </Card>

      <Card className="flex flex-col gap-4">
        <h2 className="font-semibold">Trading hours</h2>
        <SpecFields
          section="session"
          specs={SESSION}
          values={config.session ?? {}}
          defaults={defaults?.session ?? {}}
          {...common}
          onChange={(k, v) => setPart("session", k, v)}
        />
      </Card>

      <Card>
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2">
            <span>
              <span className="font-semibold">SMC rules</span>
              <span className="block text-sm text-muted">
                The thresholds that make each concept objective. The defaults were fixed before backtesting.
              </span>
            </span>
            <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <div className="mt-4 flex flex-col gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectField
                label="Enter at"
                value={(part("rules").poi as "fvg" | "ob" | "either" | "both") ?? "either"}
                error={errs["rules.poi"]}
                options={[
                  { value: "either", label: "Fair value gap or order block" },
                  { value: "fvg", label: "Fair value gap only" },
                  { value: "ob", label: "Order block only" },
                  { value: "both", label: "Where both overlap" },
                ]}
                onChange={(v) => setPart("rules", "poi", v)}
              />
              <SelectField
                label="Order block zone"
                value={(part("rules").ob_zone as "body" | "range") ?? "body"}
                options={[
                  { value: "body", label: "Candle body" },
                  { value: "range", label: "Full candle (wicks)" },
                ]}
                onChange={(v) => setPart("rules", "ob_zone", v)}
              />
              <SelectField
                label="Premium / discount of"
                value={(part("rules").pd_range as "setup" | "bias") ?? "setup"}
                options={[
                  { value: "setup", label: "The impulse leg (sweep to break)" },
                  { value: "bias", label: "The bias timeframe's dealing range" },
                ]}
                onChange={(v) => setPart("rules", "pd_range", v)}
              />
            </div>
            <SpecFields
              section="rules"
              specs={RULES}
              values={config.rules ?? {}}
              defaults={defaults?.rules ?? {}}
              {...common}
              onChange={(k, v) => setPart("rules", k, v)}
            />
          </div>
        </details>
      </Card>
    </>
  );
}
