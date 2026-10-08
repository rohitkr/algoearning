"use client";

import type { RulesConfig, SignalSource, StrategyLeg } from "@algoearning/api-types";
import { Button, Card } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { apiRequest } from "@/lib/client-api";
import { entryOf } from "@/lib/strategy";

import { NumberField, SelectField } from "./fields";

type Errs = Record<string, string>;

function leg(
  id: string,
  action: "BUY" | "SELL",
  option_type: "CE" | "PE",
  direction: "up" | "down",
  offset = 0,
): StrategyLeg {
  return {
    id,
    action,
    option_type,
    direction,
    lots: 1,
    expiry: "current_week",
    strike: { mode: "atm", offset, premium: null, points: null },
    stop_loss: action === "SELL" ? { unit: "percent", value: 40, basis: "premium" } : null,
    target: null,
    trailing: null,
    reentry_on_sl: null,
    reentry_on_target: null,
  };
}

/** Ready-made legs for a tip's direction (the tip's own option is never used: only bullish or bearish). */
export const TIP_SETUPS: Record<string, { label: string; legs: StrategyLeg[] }> = {
  sell_opposite: {
    label: "Sell the opposite side (ATM)",
    legs: [leg("BULL", "SELL", "PE", "up"), leg("BEAR", "SELL", "CE", "down")],
  },
  credit_spread: {
    label: "Credit spread (sell ATM, buy 4 strikes out)",
    legs: [
      leg("BULL", "SELL", "PE", "up"),
      leg("BULLH", "BUY", "PE", "up", 4),
      leg("BEAR", "SELL", "CE", "down"),
      leg("BEARH", "BUY", "CE", "down", 4),
    ],
  },
  follow: {
    label: "Follow the tip (buy ATM CE / PE)",
    legs: [leg("BULL", "BUY", "CE", "up"), leg("BEAR", "BUY", "PE", "down")],
  },
};

/** "On a Telegram tip" (ADR 0025): which source, how fresh a tip must be, and what the channel's own exit does. */
export function TipEntry({
  config,
  errs,
  onChange,
}: {
  config: RulesConfig;
  errs: Errs;
  onChange: (c: RulesConfig) => void;
}) {
  const { getToken } = useAuth();
  const e = entryOf(config);
  const [sources, setSources] = useState<SignalSource[] | null>(null);
  const token = useRef(getToken); // loaded once: a new getToken identity must not reload the list
  useEffect(() => {
    let alive = true;
    apiRequest<SignalSource[]>("GET", "/v1/signal-sources", undefined, token.current)
      .then((s) => alive && setSources(Array.isArray(s) ? s.filter((x) => x.chat_id !== null) : []))
      .catch(() => alive && setSources([]));
    return () => {
      alive = false;
    };
  }, []);
  const setEntry = (p: Partial<typeof e>) => onChange({ ...config, entry: { ...e, ...p } });

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="font-semibold">Telegram tip</h2>
        <p className="text-sm text-muted">
          Only the tip&apos;s direction is used: BUY CE / SELL PE is bullish and trades the legs marked{" "}
          <span className="font-medium text-foreground">Up signal</span>; BUY PE / SELL CE is bearish and
          trades the <span className="font-medium text-foreground">Down signal</span> legs. Tips come from a
          third party: you decide whether to trade them.
        </p>
      </div>
      {sources !== null && sources.length === 0 ? (
        <p className="text-sm">
          No Telegram source yet.{" "}
          <Link href="/signals" className="text-primary underline">
            Connect one on the Signals page
          </Link>
          .
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SelectField
            label="Source"
            value={e.source_id ?? ""}
            error={errs["entry.source_id"]}
            options={[
              { value: "", label: sources === null ? "Loading…" : "Choose…" },
              ...(sources ?? []).map((s) => ({ value: s.id, label: s.chat_title ?? s.label ?? "Telegram" })),
            ]}
            onChange={(v) => setEntry({ source_id: v || null })}
          />
          <NumberField
            label="Ignore tips older than"
            value={e.max_tip_age_s}
            min={10}
            max={3600}
            suffix="s"
            error={errs["entry.max_tip_age_s"]}
            onChange={(v) => setEntry({ max_tip_age_s: v ?? Number.NaN })}
          />
          <SelectField
            label="When the channel's tip ends"
            value={e.on_tip_exit}
            options={[
              { value: "close", label: "Close my trade (SL hit or target 3)" },
              { value: "ignore", label: "Keep it (my SL / target / exit time)" },
            ]}
            onChange={(on_tip_exit) => setEntry({ on_tip_exit })}
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Quick legs:</span>
        {Object.entries(TIP_SETUPS).map(([k, s]) => (
          <Button key={k} size="sm" variant="secondary" onClick={() => onChange({ ...config, legs: s.legs })}>
            {s.label}
          </Button>
        ))}
      </div>
    </Card>
  );
}
