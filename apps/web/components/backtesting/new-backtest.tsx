"use client";

import type { Backtest, HistoryCoverage, Strategy } from "@algoearning/api-types";
import { Button, Card, CardTitle, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { NumberField, inputClass } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

/** Pick a strategy and a range; the worker replays it over stored history and the result page fills in. */
export function NewBacktest({
  strategies,
  coverage,
}: {
  strategies: Strategy[];
  coverage: HistoryCoverage[];
}) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [sid, setSid] = useState(strategies[0]?.id ?? "");
  const cov = (u: string) => coverage.find((c) => c.underlying === u);
  const strategy = strategies.find((s) => s.id === sid);
  const c = strategy ? cov(strategy.config.underlying) : undefined;
  // default to the range that has option data (the whole point of a backtest), else the index range
  const [from, setFrom] = useState(c?.option_from ?? c?.index_from ?? "");
  const [to, setTo] = useState(c?.option_to ?? c?.index_to ?? "");
  const [multiplier, setMultiplier] = useState(1);
  const [slippage, setSlippage] = useState(0.05);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);

  function pick(id: string) {
    setSid(id);
    const st = strategies.find((s) => s.id === id);
    const cv = st ? cov(st.config.underlying) : undefined;
    if (cv) {
      setFrom(cv.option_from ?? cv.index_from ?? "");
      setTo(cv.option_to ?? cv.index_to ?? "");
    }
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const b = await apiRequest<Backtest>(
        "POST",
        "/v1/backtests",
        { strategy_id: sid, start_date: from, end_date: to, multiplier, slippage_pct: slippage },
        getToken,
      );
      router.push(`/backtesting/${b.id}`);
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      setError({ text: api?.message ?? (e as Error).message, upgrade: api?.body?.code === "plan_feature" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <CardTitle>New backtest</CardTitle>
      {strategies.length === 0 ? (
        <p className="text-sm text-muted">
          You have no strategies yet.{" "}
          <Link href="/builder" className="text-primary-text underline">
            Build one
          </Link>
          .
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <label className="flex flex-col gap-1 text-xs font-medium text-muted lg:col-span-2">
              Strategy
              <select className={inputClass} value={sid} onChange={(e) => pick(e.target.value)}>
                {strategies.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {s.config.underlying}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted">
              From
              <input
                type="date"
                className={inputClass}
                value={from}
                min={c?.index_from ?? undefined}
                max={to || undefined}
                onChange={(e) => setFrom(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted">
              To
              <input
                type="date"
                className={inputClass}
                value={to}
                min={from || undefined}
                max={c?.index_to ?? undefined}
                onChange={(e) => setTo(e.target.value)}
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <NumberField
                label="Multiplier"
                min={1}
                value={multiplier}
                onChange={(v) => setMultiplier(v ?? 1)}
              />
              <NumberField
                label="Slippage"
                suffix="%"
                step={0.01}
                min={0}
                value={slippage}
                onChange={(v) => setSlippage(v ?? 0)}
              />
            </div>
          </div>
          {c ? (
            <p className="text-xs text-muted">
              {c.underlying} history: index {c.index_from} to {c.index_to} ({c.index_days} days)
              {c.option_from
                ? `; options ${c.option_from} to ${c.option_to} (${c.option_days} days, ${c.expiries} expiries)`
                : "; no option prices yet"}
              . Days without option prices cannot trade.
            </p>
          ) : (
            strategy && (
              <p className="text-xs text-loss">No stored history for {strategy.config.underlying} yet.</p>
            )
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={start} disabled={busy || !sid || !from || !to}>
              {busy ? "Starting…" : "Run backtest"}
            </Button>
            {error && (
              <p role="alert" className={cn("text-sm text-loss")}>
                {error.text}{" "}
                {error.upgrade && (
                  <Link href="/subscription" className="text-primary-text underline">
                    See plans
                  </Link>
                )}
              </p>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
