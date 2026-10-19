"use client";

import type { BacktestPreview, SignalSource, Strategy, StrategyConfig } from "@algoearning/api-types";
import { Button, Card, CardTitle } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { FlaskConical, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { NumberField, inputClass } from "@/components/builder/fields";
import { BacktestResultView, type BacktestResultData } from "@/components/backtesting/result-view";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

const FIRST_TIP = "2026-07-09"; // the channel's first trade message

type Entry = { mode?: string; source_id?: string | null };

/** Does this strategy enter on tips from this source (a rules config, or any plan of a plans config)? */
export function tradesSource(config: StrategyConfig, sourceId: string): boolean {
  const c = config as { entry?: Entry; plans?: { entry?: Entry }[] };
  const entries = c.plans ? c.plans.map((p) => p.entry) : [c.entry];
  return entries.some((e) => e?.mode === "tip" && e.source_id === sourceId);
}

function today(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

/** Backtest a saved Telegram strategy over this channel's stored tips (the same runner the engine trades live). */
export function TipStrategyBacktest({ source }: { source: SignalSource }) {
  const { getToken } = useAuth();
  const token = useRef(getToken); // loaded once: a new getToken identity must not reload the list
  const [strategies, setStrategies] = useState<Strategy[] | null>(null);
  const [pick, setPick] = useState("");
  const [from, setFrom] = useState(FIRST_TIP);
  const [to, setTo] = useState(today());
  const [multiplier, setMultiplier] = useState(1);
  const [slippage, setSlippage] = useState(0.05);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);
  const [out, setOut] = useState<{ res: BacktestResultData; name: string; from: string; to: string } | null>(
    null,
  );

  useEffect(() => {
    let alive = true;
    apiRequest<{ items: Strategy[] }>("GET", "/v1/strategies?limit=100", undefined, token.current)
      .then((p) => {
        if (!alive) return;
        const mine = p.items.filter((s) => s.status !== "archived" && tradesSource(s.config, source.id));
        setStrategies(mine);
        if (mine[0]) setPick(mine[0].id);
      })
      .catch(() => alive && setStrategies([]));
    return () => {
      alive = false;
    };
  }, [source.id]);

  const chosen = strategies?.find((s) => s.id === pick) ?? null;

  async function run() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiRequest<BacktestPreview>(
        "POST",
        `/v1/signal-sources/${source.id}/backtest`,
        { config: chosen.config, start_date: from, end_date: to, multiplier, slippage_pct: slippage },
        getToken,
      );
      setOut({ res: r.result as unknown as BacktestResultData, name: chosen.name, from, to });
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      setError({ text: api?.message ?? (e as Error).message, upgrade: api?.body?.code === "plan_feature" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <CardTitle>Backtest a Telegram strategy</CardTitle>
        <p className="text-sm text-muted">
          Replays one of your strategies that trade {source.chat_title ?? "this channel"}&apos;s tips over the
          stored tips and option prices, with the same rules the engine uses live: each tip is seen from the
          minute it was posted and filled at the next minute&apos;s open. The channel&apos;s own &quot;target
          done&quot; and &quot;SL hit&quot; messages are not replayed: your strategy&apos;s exits decide.
        </p>
      </div>
      {strategies !== null && strategies.length === 0 ? (
        <p className="text-sm">
          No strategy trades this channel&apos;s tips yet.{" "}
          <Link href="/builder" className="text-primary underline">
            Build one
          </Link>{" "}
          with entry &quot;On a Telegram tip&quot;.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
          <label className="col-span-2 flex flex-col gap-1 text-xs font-medium text-muted">
            Strategy
            <select className={inputClass} value={pick} onChange={(e) => setPick(e.target.value)}>
              {strategies === null && <option value="">Loading…</option>}
              {(strategies ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
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
              onChange={(e) => setFrom(e.target.value)}
            />
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
          <div className="col-span-2 flex items-end sm:col-span-6">
            <Button onClick={run} disabled={busy || !chosen || !from || !to || from > to}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <FlaskConical className="size-4" aria-hidden />
              )}{" "}
              {busy ? "Backtesting..." : out ? "Backtest again" : "Backtest"}
            </Button>
          </div>
        </div>
      )}
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
        <BacktestResultView
          res={out.res}
          meta={{
            title: `${out.name} (Telegram backtest)`,
            underlying: chosen?.config.underlying ?? "NIFTY",
            from: out.from,
            to: out.to,
            multiplier,
            slippage,
          }}
        />
      )}
    </Card>
  );
}
