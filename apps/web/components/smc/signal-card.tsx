import { formatNumber } from "@algoearning/shared";
import { Pnl, StatusPill, cn } from "@algoearning/ui";

/** An SMC scalper's signal as the runner wrote it down (event "smc_signal"): the option bought, the index levels and
 * the setup that produced it, one step per line. */
export type SmcSignal = {
  signal?: string; // "BUY CE" | "BUY PE"
  contract?: string;
  premium?: number | null;
  entry?: number;
  stop_loss?: number;
  tp1?: number;
  tp2?: number;
  tp3?: number;
  risk?: number;
  reward?: number;
  rr?: string;
  reason?: string;
  net?: number | null; // backtests: the signal's result
  exits?: string[];
};

const fmt = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v));

export function isSmcSignal(event: string): boolean {
  return event === "smc_signal";
}

export function SignalCard({ s, when }: { s: SmcSignal; when?: string }) {
  const bullish = s.signal?.endsWith("CE");
  const levels: [string, number | undefined, string][] = [
    ["Entry", s.entry, ""],
    ["Stop-loss", s.stop_loss, "text-loss"],
    ["TP1", s.tp1, "text-profit"],
    ["TP2", s.tp2, "text-profit"],
    ["TP3", s.tp3, "text-profit"],
  ];
  return (
    <article className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
      <header className="flex flex-wrap items-center gap-2">
        <StatusPill tone={bullish ? "success" : "danger"}>{s.signal ?? "Signal"}</StatusPill>
        <span className="font-semibold">{s.contract}</span>
        {s.premium != null && <span className="text-sm text-muted">@ ₹{fmt(s.premium)}</span>}
        {s.rr && <StatusPill tone="info">R:R {s.rr}</StatusPill>}
        {when && <span className="ml-auto text-xs text-muted tabular-nums">{when}</span>}
      </header>
      <dl className="grid grid-cols-3 gap-2 text-sm tabular-nums sm:grid-cols-7">
        {levels.map(([label, v, tone]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className={cn("font-medium", tone)}>{fmt(v)}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-muted">Risk</dt>
          <dd className="font-medium">{fmt(s.risk)} pts</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Reward (TP3)</dt>
          <dd className="font-medium">{fmt(s.reward)} pts</dd>
        </div>
      </dl>
      {s.reason && (
        <ol className="flex flex-col gap-1 text-sm">
          {s.reason.split(" · ").map((step, i) => (
            <li key={i} className="flex gap-2">
              <span
                aria-hidden
                className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-muted"
              >
                {i + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      )}
      {(s.net != null || (s.exits && s.exits.length > 0)) && (
        <footer className="flex flex-wrap items-center gap-3 border-t border-border pt-2 text-sm">
          {s.net != null && (
            <span>
              Result <Pnl value={s.net} className="font-semibold" />
            </span>
          )}
          {s.exits && s.exits.length > 0 && <span className="text-xs text-muted">{s.exits.join(" · ")}</span>}
        </footer>
      )}
    </article>
  );
}
