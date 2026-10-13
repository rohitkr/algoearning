import type { Run } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

const dt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
export const fmt = (v: string | null | undefined) => (v ? dt.format(new Date(v)) : "–");

const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
/** A time of day in Indian time, as the exchange keeps it. */
export const istClock = (v: string | null | undefined) => (v ? clock.format(new Date(v)) : null);

/** What the open P&L is as of: the oldest quote behind the open positions. */
export function PricesAsOf({ run }: { run: Pick<Run, "prices_at" | "open_positions"> }) {
  const at = istClock(run.prices_at);
  if (!at || run.open_positions === 0) return null;
  return <p className="text-[11px] text-muted">prices as of {at} IST</p>;
}

/** Shown while a running strategy holds positions, the exchange is open and no live price has arrived for 2 minutes. */
export function StalePricesNotice({ run }: { run: Pick<Run, "prices_stale" | "prices_at"> }) {
  if (!run.prices_stale) return null;
  const at = istClock(run.prices_at);
  return (
    <p role="alert" className="rounded-lg border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss">
      Live prices have stopped{at ? ` (last price ${at} IST)` : ""} while the market is open. The profit and
      loss below is not live and stop-losses and limits are not being watched. Check the price feed under
      Monitor, Market data, and be ready to exit in Zerodha.
    </p>
  );
}

export function RunStatusPill({ run }: { run: Pick<Run, "status" | "engine_stale"> }) {
  if (run.engine_stale) return <StatusPill tone="warning">Engine not responding</StatusPill>;
  const map = {
    pending: { tone: "info", text: "Starting" },
    running: { tone: "success", text: "Running" },
    stopping: { tone: "warning", text: "Squaring off" },
    stopped: { tone: "neutral", text: "Stopped" },
    completed: { tone: "neutral", text: "Completed" },
    error: { tone: "danger", text: "Error" },
  } as const;
  const m = map[run.status];
  return <StatusPill tone={m.tone}>{m.text}</StatusPill>;
}

export function ModePill({ mode, dryRun = false }: { mode: Run["mode"]; dryRun?: boolean }) {
  if (mode === "live" && dryRun) return <StatusPill tone="warning">Live · dry run</StatusPill>;
  return mode === "live" ? (
    <StatusPill tone="danger">LIVE</StatusPill>
  ) : (
    <StatusPill tone="info">Paper</StatusPill>
  );
}
