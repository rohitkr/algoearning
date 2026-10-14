"use client";

import type {
  ConfigValidation,
  RangeBreakoutConfig,
  SmcScalpConfig,
  Strategy,
  StrategyCatalog,
  StrategyConfig,
  ZeroDteConfig,
} from "@algoearning/api-types";
import { Button, Card, CardTitle, StatusPill, Switch, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { ArrowLeft, Save } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { UnsavedDialog } from "./unsaved-dialog";
import { createPortal } from "react-dom";

import { ApiRequestError, apiRequest } from "@/lib/client-api";
import {
  KIND_LABEL,
  describeConfig,
  issueKey,
  issueLabel,
  issueMap,
  newPlan,
  type Underlying,
} from "@/lib/strategy";

import { Check, Field, NumberField, SelectField, TimeField, inputClass } from "./fields";
import { PlansEditor } from "./plans-editor";
import { SmcParams } from "./smc-params";
import { StrategyTest } from "./strategy-test";

type Errs = Record<string, string>;
type Proven = RangeBreakoutConfig | ZeroDteConfig;

/** Parameter forms of the two proven strategies: one row per field. */
type ParamSpec<C> = {
  key: keyof C & string;
  label: string;
  type: "time" | "int" | "float" | "bool" | "optional_int";
  suffix?: string;
  hint?: string;
};
const RANGE_PARAMS: ParamSpec<RangeBreakoutConfig>[] = [
  { key: "range_start", label: "Range from", type: "time" },
  { key: "range_end", label: "Range until", type: "time" },
  { key: "last_entry", label: "Last entry", type: "time" },
  { key: "exit_time", label: "Exit time", type: "time", hint: "On expiry day, or the entry day if intraday" },
  { key: "itm_points", label: "In the money by", type: "int", suffix: "pts" },
  { key: "stop_loss_pct", label: "Stop-loss (index move)", type: "float", suffix: "%" },
  { key: "lots", label: "Lots", type: "int" },
  { key: "expiry_offset", label: "Expiry", type: "int", hint: "0 = next weekly expiry, 1 = the one after" },
  { key: "hedge_width", label: "Hedge wing", type: "optional_int", suffix: "pts", hint: "Empty = unhedged" },
  { key: "reentry", label: "Re-enter once at cost after a stop-loss", type: "bool" },
  { key: "intraday_only", label: "Exit the same day (intraday only)", type: "bool" },
];
const ZERO_DTE_PARAMS: ParamSpec<ZeroDteConfig>[] = [
  { key: "first_entry", label: "Earliest entry", type: "time" },
  { key: "last_entry", label: "Latest entry", type: "time" },
  { key: "step_minutes", label: "Try entries every", type: "int", suffix: "min" },
  { key: "exit_time", label: "Exit time", type: "time" },
  { key: "lookback", label: "Judge on last", type: "int", suffix: "expiries" },
  { key: "itm_points", label: "In the money by", type: "int", suffix: "pts" },
  { key: "stop_loss_pct", label: "Stop-loss (premium)", type: "float", suffix: "%" },
  { key: "lots", label: "Lots per leg", type: "int" },
  { key: "hedge_width", label: "Hedge wing", type: "optional_int", suffix: "pts", hint: "Empty = unhedged" },
  { key: "reentry", label: "Re-enter once after a stop-loss", type: "bool" },
];

function ProvenParams<C extends Proven>({
  config,
  specs,
  hours,
  errs,
  warns,
  onChange,
}: {
  config: C;
  specs: ParamSpec<C>[];
  hours: { min?: string; max?: string };
  errs: Errs;
  warns: Errs;
  onChange: (c: C) => void;
}) {
  const set = (k: keyof C, v: unknown) => onChange({ ...config, [k]: v });
  const bools = specs.filter((s) => s.type === "bool");
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
                value={config[s.key] as string}
                {...hours}
                error={errs[s.key]}
                onChange={(v) => set(s.key, v)}
              />
            ) : (
              <NumberField
                key={s.key}
                label={s.label}
                value={config[s.key] as number | null}
                suffix={s.suffix}
                step={s.type === "float" ? 0.1 : 1}
                optional={s.type === "optional_int"}
                error={errs[s.key] ?? warns[s.key]}
                hint={s.hint}
                onChange={(v) => set(s.key, v)}
              />
            ),
          )}
      </div>
      <div className="mt-4 flex flex-col gap-2">
        {bools.map((s) => (
          <Check key={s.key} label={s.label} checked={!!config[s.key]} onChange={(on) => set(s.key, on)} />
        ))}
      </div>
    </>
  );
}

export function StrategyBuilder({ catalog, strategy }: { catalog: StrategyCatalog; strategy?: Strategy }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const presets = catalog.presets;
  const [presetId, setPresetId] = useState<string | null>(strategy ? null : (presets[0]?.id ?? null));
  const [name, setName] = useState(strategy?.name ?? "");
  const [description, setDescription] = useState(strategy?.description ?? "");
  const [config, setConfig] = useState<StrategyConfig>(
    () =>
      strategy?.config ??
      presets[0]?.config ?? { kind: "plans", underlying: "NIFTY", plans: [newPlan([], "NIFTY", true)] },
  );
  const [ready, setReady] = useState(strategy?.status === "ready");
  const [check, setCheck] = useState<ConfigValidation | null>(null);
  const [saveErrors, setSaveErrors] = useState<Errs>({});
  const [note, setNote] = useState<{ tone: "success" | "danger"; text: string; upgrade?: boolean } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  // what is saved (or, for a new strategy, what the page started with): anything different is unsaved
  const snapshot = () => JSON.stringify({ name, description, config, ready });
  const [baseline, setBaseline] = useState(snapshot);
  const dirty = snapshot() !== baseline;
  const [leaving, setLeaving] = useState<string | null>(null); // where the user was going when we stopped them
  const seq = useRef(0);

  const inst = catalog.instruments.find((i) => i.code === config.underlying);
  const hours = { min: inst?.session_open, max: inst?.session_close };
  const errs = useMemo(() => ({ ...issueMap(check?.errors), ...saveErrors }), [check, saveErrors]);
  const warns = useMemo(() => issueMap(check?.warnings), [check]);
  const summary = useMemo(() => describeConfig(config, catalog.instruments), [config, catalog.instruments]);

  // Validate on the server while editing (debounced): the same rules that saving applies.
  useEffect(() => {
    const n = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await apiRequest<ConfigValidation>("POST", "/v1/strategies/validate", { config }, getToken);
        if (n === seq.current) setCheck(r);
      } catch {
        // offline or signed out: saving reports the problem
      }
    }, 400);
    return () => clearTimeout(t);
  }, [config, getToken]);

  // Unsaved changes: stop the tab closing, in-app links and the browser's back button, and ask what to do.
  useEffect(() => {
    if (!dirty) return;
    const unload = (e: BeforeUnloadEvent) => e.preventDefault();
    const click = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || a.target)
        return;
      const url = new URL(a.href, location.href);
      if (url.origin !== location.origin || url.pathname + url.search === location.pathname + location.search)
        return;
      e.preventDefault();
      e.stopPropagation();
      setLeaving(url.pathname + url.search + url.hash);
    };
    const pop = () => {
      history.pushState(null, "", location.href); // stay here until they decide
      setLeaving("back");
    };
    history.pushState(null, "", location.href);
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", click, true);
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", click, true);
      window.removeEventListener("popstate", pop);
    };
  }, [dirty]);

  const go = useCallback(
    (where: string) => {
      // the guard is removed as the state changes, then we leave
      if (where === "back")
        history.go(-2); // the extra entry we pushed, and the page itself
      else router.push(where);
    },
    [router],
  );

  function update(c: StrategyConfig) {
    setConfig(c);
    setSaveErrors({});
    setNote(null);
  }

  function pickPreset(id: string) {
    const p = presets.find((x) => x.id === id);
    if (!p) return;
    setPresetId(id);
    update(p.config);
    if (!name || presets.some((x) => x.name === name)) setName(p.id === "blank" ? "" : p.name);
  }

  /** Saves; true when it worked. `then`: where to go afterwards (a new strategy otherwise opens its own page). */
  async function save(then?: string): Promise<boolean> {
    if (!name.trim()) {
      setNote({ tone: "danger", text: "Give the strategy a name." });
      return false;
    }
    const at = snapshot();
    setSaving(true);
    setNote(null);
    setSaveErrors({});
    const body = { name: name.trim(), description: description.trim() || null, config };
    try {
      if (strategy) {
        await apiRequest<Strategy>(
          "PATCH",
          `/v1/strategies/${strategy.id}`,
          { ...body, status: ready ? "ready" : strategy.status === "archived" ? "archived" : "draft" },
          getToken,
        );
        setBaseline(at);
        setNote({ tone: "success", text: "Saved." });
        if (then) router.push(then);
        else router.refresh();
      } else {
        const created = await apiRequest<Strategy>("POST", "/v1/strategies", body, getToken);
        if (ready) await apiRequest("PATCH", `/v1/strategies/${created.id}`, { status: "ready" }, getToken);
        setBaseline(at);
        router.push(then ?? `/builder/${created.id}?saved=1`);
      }
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      const details = Array.isArray(api?.body?.details)
        ? (api.body.details as { loc: (string | number)[]; msg: string }[])
        : [];
      const fieldErrs: Errs = {};
      for (const d of details) fieldErrs[issueKey(d.loc)] ??= d.msg;
      setSaveErrors(fieldErrs);
      setNote({
        tone: "danger",
        text: details.length
          ? "Fix the highlighted fields and save again."
          : (api?.message ?? (e as Error).message),
        upgrade: api?.body?.code === "plan_limit",
      });
      return false;
    } finally {
      setSaving(false);
    }
    return true;
  }

  // while the fixed header is showing, Save lives there instead of in the title row
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const sync = () => setScrolled(document.documentElement.hasAttribute("data-header-stuck"));
    sync();
    window.addEventListener("header-stuck", sync);
    return () => window.removeEventListener("header-stuck", sync);
  }, []);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the header lives in the layout: found after mount
    setSlot(document.getElementById("header-actions"));
  }, []);

  function discardAndLeave() {
    const where = leaving;
    if (!where) return;
    setBaseline(snapshot()); // nothing is unsaved any more: the guard comes off
    setLeaving(null);
    setTimeout(() => go(where), 0);
  }

  const allErrors = Object.entries(errs);
  const allWarnings = Object.entries(warns);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="Back to strategies">
          <Link href="/strategies">
            <ArrowLeft className="size-4" aria-hidden />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold">{strategy ? "Edit strategy" : "New strategy"}</h1>
          <p className="text-sm text-muted">
            {KIND_LABEL[config.kind]}
            {strategy && ` · version ${strategy.version}`}
          </p>
        </div>
        {strategy?.status === "archived" && <StatusPill>Archived</StatusPill>}
        <Button
          onClick={() => void save()}
          disabled={saving || !dirty}
          className={cn(scrolled && slot && "invisible")}
        >
          <Save className="size-4" aria-hidden /> {saving ? "Saving…" : "Save"}
        </Button>
      </div>

      {scrolled &&
        slot &&
        createPortal(
          <Button
            size="sm"
            onClick={() => void save()}
            disabled={saving || !dirty}
            className="animate-header-in mr-1 shrink-0"
          >
            <Save className="size-4" aria-hidden /> {saving ? "Saving…" : "Save"}
          </Button>,
          slot,
        )}

      <UnsavedDialog open={leaving !== null} onDiscard={discardAndLeave} onCancel={() => setLeaving(null)} />

      {note && (
        <p role="status" className={cn("text-sm", note.tone === "success" ? "text-profit" : "text-loss")}>
          {note.text}{" "}
          {note.upgrade && (
            <Link href="/subscription" className="text-primary-text underline">
              See plans
            </Link>
          )}
        </p>
      )}

      {!strategy && (
        <Card>
          <CardTitle>Start from</CardTitle>
          <div
            className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
            role="radiogroup"
            aria-label="Start from"
          >
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={presetId === p.id}
                onClick={() => pickPreset(p.id)}
                className={cn(
                  "rounded-xl border p-3 text-left transition-colors",
                  presetId === p.id ? "border-primary bg-accent" : "border-border hover:bg-surface-2",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold">{p.name}</span>
                  {p.config.kind === "smc_scalp" ? (
                    <StatusPill tone="info">SMC</StatusPill>
                  ) : (
                    p.config.kind !== "plans" && <StatusPill tone="info">Proven</StatusPill>
                  )}
                </span>
                <span className="mt-1 block text-xs text-muted">{p.description}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card className="flex flex-col gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name" error={!name.trim() && note?.tone === "danger" ? "Required" : undefined}>
                {(a) => (
                  <input
                    {...a}
                    className={inputClass}
                    value={name}
                    maxLength={120}
                    placeholder="e.g. Nifty morning straddle"
                    onChange={(e) => {
                      setName(e.target.value);
                    }}
                  />
                )}
              </Field>
              {config.kind !== "plans" && (
                <SelectField
                  label="Underlying"
                  value={config.underlying}
                  error={errs.underlying}
                  hint={
                    inst
                      ? `Lot size ${inst.lot_size} · strikes every ${inst.strike_step} · ${inst.weekly_expiry ? "weekly" : "monthly"} expiries · trades ${inst.session_open}–${inst.session_close}`
                      : undefined
                  }
                  options={catalog.instruments.map((i) => ({
                    value: i.code as Underlying,
                    label: `${i.code} · ${i.name}`,
                  }))}
                  onChange={(v) => {
                    update({ ...config, underlying: v });
                  }}
                />
              )}
            </div>
            <Field label="Notes (optional)">
              {(a) => (
                <textarea
                  {...a}
                  className={cn(inputClass, "h-auto min-h-16 py-2")}
                  value={description}
                  maxLength={5000}
                  onChange={(e) => {
                    setDescription(e.target.value);
                  }}
                />
              )}
            </Field>
          </Card>

          {config.kind === "plans" ? (
            <PlansEditor config={config} catalog={catalog} errs={errs} warns={warns} onChange={update} />
          ) : config.kind === "smc_scalp" ? (
            <SmcParams
              config={config}
              defaults={
                presets.find((p) => p.config.kind === "smc_scalp")?.config as SmcScalpConfig | undefined
              }
              hours={hours}
              errs={errs}
              warns={warns}
              onChange={update}
            />
          ) : (
            <Card>
              <h2 className="mb-4 font-semibold">Parameters</h2>
              {config.kind === "range_breakout" ? (
                <ProvenParams
                  config={config}
                  specs={RANGE_PARAMS}
                  hours={hours}
                  errs={errs}
                  warns={warns}
                  onChange={update}
                />
              ) : config.kind === "zero_dte" ? (
                <ProvenParams
                  config={config}
                  specs={ZERO_DTE_PARAMS}
                  hours={hours}
                  errs={errs}
                  warns={warns}
                  onChange={update}
                />
              ) : null}
            </Card>
          )}
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-4" aria-label="Summary">
          <Card>
            <CardTitle>Summary</CardTitle>
            <ul className="mt-2 flex flex-col gap-2 text-sm">
              {summary.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </Card>
          <Card className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle>Checks</CardTitle>
              {check == null ? (
                <StatusPill>Checking…</StatusPill>
              ) : allErrors.length ? (
                <StatusPill tone="danger">{allErrors.length} to fix</StatusPill>
              ) : (
                <StatusPill tone="success">Ready to save</StatusPill>
              )}
            </div>
            {allErrors.length > 0 && (
              <ul className="flex flex-col gap-1.5 text-sm">
                {allErrors.map(([k, msg]) => (
                  <li key={k} className="text-loss">
                    <span className="font-medium">{issueLabel(k)}:</span> {msg}
                  </li>
                ))}
              </ul>
            )}
            {allWarnings.length > 0 && (
              <ul className="flex flex-col gap-1.5 text-sm">
                {allWarnings.map(([k, msg]) => (
                  <li key={k} className="text-warning">
                    <span className="font-medium">{issueLabel(k)}:</span> {msg}.{" "}
                    <Link href="/subscription" className="underline">
                      Upgrade
                    </Link>{" "}
                    to deploy it.
                  </li>
                ))}
              </ul>
            )}
            <label className="flex items-center justify-between gap-3 border-t border-border pt-3 text-sm">
              <span>
                Ready to deploy
                <span className="block text-xs text-muted">Only ready strategies can be deployed</span>
              </span>
              <Switch
                checked={ready}
                onCheckedChange={(on) => {
                  setReady(on);
                }}
                aria-label="Ready to deploy"
              />
            </label>
          </Card>
        </aside>
      </div>
      <StrategyTest config={config} blocked={check == null || allErrors.length > 0} />
    </div>
  );
}
