"use client";

import type { MarketSnapshot } from "@algoearning/api-types";
import { formatNumber, formatPercent, pnlTone } from "@algoearning/shared";
import { StatusPill, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/client-api";

const KEYS = ["NIFTY", "SENSEX"];
const TONE = { profit: "text-profit", loss: "text-loss", flat: "text-muted" } as const;

/** Index prices in the top bar, from the platform feed (refreshed every few seconds while the tab is visible). */
export function MarketTicker() {
  const { getToken } = useAuth();
  const [snap, setSnap] = useState<MarketSnapshot | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const qs = KEYS.map((k) => `keys=${k}`).join("&");
        const s = await apiRequest<MarketSnapshot>("GET", `/v1/market/snapshot?${qs}`, undefined, getToken);
        if (alive) setSnap(s);
      } catch {
        // the ticker is decoration: an unreachable API is shown elsewhere
      }
    };
    void load();
    const t = setInterval(load, 3000);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", load);
    };
  }, [getToken]);

  if (!snap) return null;
  if (snap.feed_status === "down") return <StatusPill tone="neutral">Prices offline</StatusPill>;
  return (
    <div className="flex items-center gap-3 text-xs" aria-label="Index prices">
      {KEYS.map((k) => {
        const q = snap.quotes.find((x) => x.key === k);
        if (!q) return null;
        const chg = q.prev_close ? ((q.ltp - q.prev_close) / q.prev_close) * 100 : null;
        return (
          <span key={k} className="whitespace-nowrap">
            <span className="font-medium">{k}</span>{" "}
            <span className="tabular-nums">{formatNumber(q.ltp)}</span>
            {chg != null && (
              <span className={cn("ml-1 tabular-nums", TONE[pnlTone(chg)])}>{formatPercent(chg)}</span>
            )}
          </span>
        );
      })}
      {snap.feed_status === "simulated" && <StatusPill tone="warning">Simulated</StatusPill>}
    </div>
  );
}
