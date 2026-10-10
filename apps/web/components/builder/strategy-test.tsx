"use client";

import type { BacktestPreview, HistoryCoverage, StrategyConfig } from "@algoearning/api-types";
import { Button, Card, CardTitle, StatusPill } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { FlaskConical } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { BacktestResultView, type BacktestResultData } from "@/components/backtesting/result-view";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

import { NumberField, inputClass } from "./fields";

const addDays = (iso: string, days: number) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

/** Test the strategy as it stands in the builder, without saving it: change a parameter above, test again. */
export function StrategyTest({ config, blocked }: { config: StrategyConfig; blocked: boolean }) {
  const { getToken } = useAuth();
  const [cov, setCov] = useState<HistoryCoverage | null | undefined>(undefined); // undefined: still loading
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [multiplier, setMultiplier] = useState(1);
  const [slippage, setSlippage] = useState(0.05);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);
  const [out, setOut] = useState<{ res: BacktestResultData; config: string; range: string } | null>(null);

  const underlying = config.underlying;
  // the range that has option data (the whole point of a test), the last month of it
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const all = await apiRequest<HistoryCoverage[]>("GET", "/v1/backtests/coverage", undefined, getToken);
        if (!live) return;
        const c = all.find((x) => x.underlying === underlying) ?? null;
        setCov(c);
        const end = c?.option_to ?? c?.index_to ?? "";
        const first = c?.option_from ?? c?.index_from ?? "";
        if (end) {
          setTo(end);
          const month = addDays(end, -30);
          setFrom(first && month < first ? first : month);
        }
      } catch {
        if (live) setCov(null); // slow or unavailable: the dates can still be typed
      }
    })();
    return () => {
      live = false;
    };
  }, [underlying, getToken]);

  const stale = out != null && out.config !== JSON.stringify(config);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await apiRequest<BacktestPreview>(
        "POST",
        "/v1/backtests/preview",
        { config, start_date: from, end_date: to, multiplier, slippage_pct: slippage },
        getToken,
      );
      setOut({
        res: r.result as unknown as BacktestResultData,
        config: JSON.stringify(config),
        range: `${from} to ${to}`,
      });
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      setError({ text: api?.message ?? (e as Error).message, upgrade: api?.body?.code === "plan_feature" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4" id="test">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle>Test before saving</CardTitle>
          <p className="text-sm text-muted">
            Replays this strategy exactly as it is on screen over stored history. Nothing is saved: change a
            parameter above and test again.
          </p>
        </div>
        {stale && <StatusPill tone="warning">Parameters changed since this result</StatusPill>}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          From
          <input type="date" className={inputClass} value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          To
          <input type="date" className={inputClass} value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <NumberField
          label="Multiplier"
          value={multiplier}
          step={1}
          onChange={(v) => setMultiplier(Math.max(1, v ?? 1))}
        />
        <NumberField
          label="Slippage"
          value={slippage}
          suffix="%"
          step={0.01}
          onChange={(v) => setSlippage(v ?? 0)}
        />
        <div className="flex items-end">
          <Button onClick={run} disabled={busy || blocked || !from || !to || from > to}>
            <FlaskConical className="size-4" aria-hidden />{" "}
            {busy ? "Testing…" : out ? "Test again" : "Test now"}
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted">
        {cov === undefined
          ? "Looking up what history is stored…"
          : cov
            ? `Stored ${underlying} history: ${cov.option_from ?? "no options"} to ${cov.option_to ?? ""} with option prices.`
            : "Stored-history range is not available: pick the dates yourself."}{" "}
        {blocked && <span className="text-loss"> Fix the checks on the right first.</span>}
      </p>
      {error && (
        <p role="alert" className="text-sm text-loss">
          {error.text}{" "}
          {error.upgrade && (
            <Link href="/subscription" className="text-primary-text underline">
              See plans
            </Link>
          )}
        </p>
      )}
      {out && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            Result for {out.range}
            {stale ? " (before your last change)" : ""}.
          </p>
          <BacktestResultView res={out.res} />
        </div>
      )}
    </Card>
  );
}
