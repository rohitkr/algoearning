"use client";

import type { SignalSource, TipReplay, TipTrade } from "@algoearning/api-types";
import { Button, Card, StatusPill, type StatusTone, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";

const FIRST_TIP = "2026-07-09"; // the channel's first trade message
const inr = (v: number | null | undefined, digits = 0) =>
  v == null
    ? "–"
    : `${v < 0 ? "−" : ""}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: digits })}`;
const pts = (v: number | null | undefined) =>
  v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
const tone = (v: number) => (v > 0 ? "text-profit" : v < 0 ? "text-loss" : "text-muted");
const when = new Intl.DateTimeFormat("en-IN", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata",
}); // prettier-ignore
const clock = new Intl.DateTimeFormat("en-IN", {
  hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata",
}); // prettier-ignore

const ENTRY: Record<TipTrade["entry"], { tone: StatusTone; text: (t: TipTrade) => string }> = {
  IN_ZONE: { tone: "success", text: () => "Bought in range" },
  CHASED: { tone: "warning", text: (t) => `Chased +${pts(t.above_zone)}` },
  NOT_PLACED: { tone: "danger", text: () => "Not placed" },
  NO_DATA: { tone: "neutral", text: () => "No data" },
};
const CHANNEL: Record<TipTrade["channel_status"], string> = {
  OPEN: "open",
  T1: "target 1",
  T2: "target 2",
  T3: "target 3",
  SL_HIT: "SL hit",
};

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-base font-semibold tabular-nums">{children}</div>
    </div>
  );
}

function Trade({ t }: { t: TipTrade }) {
  const e = ENTRY[t.entry];
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">
          {t.tip}{" "}
          <span className="text-xs font-normal text-muted">
            {when.format(new Date(t.date))}
            {t.expiry ? ` · expiry ${t.expiry} (inferred)` : ""}
          </span>
        </span>
        <span className="flex items-center gap-2">
          <StatusPill tone={e.tone}>{e.text(t)}</StatusPill>
          {t.entry_price != null && (
            <span className={cn("font-semibold tabular-nums", tone(t.net))}>{inr(t.net)}</span>
          )}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs tabular-nums sm:grid-cols-4">
        <span>
          <span className="text-muted">Tip entry </span>
          {pts(t.entry_low)}–{pts(t.entry_high)}
        </span>
        <span>
          <span className="text-muted">SL </span>
          {pts(t.stop_loss)}
        </span>
        <span>
          <span className="text-muted">Targets </span>
          {t.targets.map(pts).join(" / ")}
        </span>
        <span>
          <span className="text-muted">Price at signal </span>
          {pts(t.price_at_signal)}
        </span>
      </div>
      <p className="text-xs text-muted">{t.note}</p>
      {t.exits.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-xs tabular-nums">
          <li>
            Bought {t.qty} @ {pts(t.entry_price)} at{" "}
            {t.entry_time ? clock.format(new Date(t.entry_time)) : "–"}
          </li>
          {t.exits.map((x, i) => (
            <li key={i}>
              {x.reason}: sold {x.qty} @ {pts(x.price)} at {clock.format(new Date(x.time))}
            </li>
          ))}
          <li className={cn(t.stopped && !(t.peak_points && t.peak_points > 0) && "text-loss")}>
            {t.stopped ? "Before the stop: " : "Highest price: "}
            {t.peak_points != null && t.peak_points > 0
              ? `market moved our way, up to ${pts(t.peak_price)} (+${pts(t.peak_points)})${t.peak_time ? ` at ${clock.format(new Date(t.peak_time))}` : ""}`
              : "market never moved our way, it went straight down"}
            {t.breakeven_time
              ? `; stop moved to cost at ${clock.format(new Date(t.breakeven_time))}${t.trail_moves ? `, then trailed ${t.trail_moves}×` : ""}, ended at ${pts(t.final_stop)}`
              : "; the move never reached the stop-to-cost trigger"}
          </li>
          <li className="text-muted">
            Gross {inr(t.gross)} − charges {inr(t.charges)} · the channel called it:{" "}
            {CHANNEL[t.channel_status]}
          </li>
        </ul>
      )}
    </li>
  );
}

/** Every tip the chat gave since its first one, bought exactly as the tip says over stored history (ADR 0025). */
export function TipReplayView({ source }: { source: SignalSource }) {
  const { getToken } = useAuth();
  const [start, setStart] = useState(FIRST_TIP);
  const [lots, setLots] = useState(3);
  const [niftyBuffer, setNiftyBuffer] = useState(5);
  const [sensexBuffer, setSensexBuffer] = useState(10);
  const [niftyCost, setNiftyCost] = useState(10);
  const [sensexCost, setSensexCost] = useState(20);
  const [niftyTrail, setNiftyTrail] = useState(10);
  const [sensexTrail, setSensexTrail] = useState(20);
  const [data, setData] = useState<TipReplay | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [only, setOnly] = useState<"all" | "traded" | "skipped">("all");

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const q = new URLSearchParams({
        start, lots: String(lots), nifty_buffer: String(niftyBuffer), sensex_buffer: String(sensexBuffer),
        nifty_breakeven: String(niftyCost), sensex_breakeven: String(sensexCost),
        nifty_trail: String(niftyTrail), sensex_trail: String(sensexTrail),
      }); // prettier-ignore
      setData(
        await apiRequest<TipReplay>(
          "GET",
          `/v1/signal-sources/${source.id}/replay?${q}`,
          undefined,
          getToken,
        ),
      );
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const s = data?.summary as Record<string, number | null | Record<string, number>> | undefined;
  const entries = (s?.entries ?? {}) as Record<string, number>;
  const shown = (data?.trades ?? []).filter(
    (t) => only === "all" || (only === "traded") === (t.entry_price != null),
  );
  const field = "h-8 w-20 rounded border border-border bg-surface px-2 text-sm";

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="font-semibold">
          Replay the tips <span className="text-sm font-normal text-muted">as given</span>
        </h2>
        <p className="text-sm text-muted">
          Buys each tip&apos;s option at the first price after the message, with the tip&apos;s own stop-loss
          and targets (a third of the lots at the first two, everything left at the last). Once the option is
          the set number of points above your entry the stop moves to cost, then trails the highest price;
          whatever is left leaves at 15:15. If the option already moved above the entry range it is bought
          only within the buffer and marked <em>chased</em>; beyond it, no order.
        </p>
      </div>
      <form
        className="flex flex-wrap items-end gap-3 text-xs"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label className="flex flex-col gap-1">
          From
          <input
            type="date"
            className={cn(field, "w-36")}
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          Lots
          <input
            type="number"
            min={1}
            max={100}
            className={field}
            value={lots}
            onChange={(e) => setLots(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Nifty buffer (pts)
          <input
            type="number"
            min={0}
            className={field}
            value={niftyBuffer}
            onChange={(e) => setNiftyBuffer(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Sensex buffer (pts)
          <input
            type="number"
            min={0}
            className={field}
            value={sensexBuffer}
            onChange={(e) => setSensexBuffer(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Nifty SL to cost after (pts)
          <input
            type="number"
            min={1}
            className={field}
            value={niftyCost}
            onChange={(e) => setNiftyCost(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Sensex SL to cost after (pts)
          <input
            type="number"
            min={1}
            className={field}
            value={sensexCost}
            onChange={(e) => setSensexCost(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Nifty trail (pts, 0 = off)
          <input
            type="number"
            min={0}
            className={field}
            value={niftyTrail}
            onChange={(e) => setNiftyTrail(Number(e.target.value))}
          />
        </label>
        <label className="flex flex-col gap-1">
          Sensex trail (pts, 0 = off)
          <input
            type="number"
            min={0}
            className={field}
            value={sensexTrail}
            onChange={(e) => setSensexTrail(Number(e.target.value))}
          />
        </label>
        <Button type="submit" disabled={busy}>
          {busy ? "Replaying…" : data ? "Replay again" : "Run replay"}
        </Button>
      </form>
      {error && (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      )}

      {data && s && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Net P&L">
              <span className={tone(Number(s.net_pnl))}>{inr(Number(s.net_pnl))}</span>
            </Stat>
            <Stat label="Tips / bought">
              {String(s.tips)} / {String(s.traded)}
            </Stat>
            <Stat label="Win rate">
              {s.win_rate == null ? "–" : `${Math.round(Number(s.win_rate) * 100)}%`}
            </Stat>
            <Stat label="Max drawdown">{inr(Number(s.max_drawdown))}</Stat>
            <Stat label="Bought in range">{entries.IN_ZONE ?? 0}</Stat>
            <Stat label="Chased (in buffer)">{entries.CHASED ?? 0}</Stat>
            <Stat label="Not placed (too high)">{entries.NOT_PLACED ?? 0}</Stat>
            <Stat label="No data">{entries.NO_DATA ?? 0}</Stat>
            <Stat label="Gross / charges">
              {inr(Number(s.gross_pnl))} / {inr(Number(s.charges))}
            </Stat>
            <Stat label="Avg win / loss">
              {inr(s.avg_win as number | null)} / {inr(s.avg_loss as number | null)}
            </Stat>
            <Stat label="Stopped out">{String(s.stopped_out)}</Stat>
            <Stat label="…after moving our way">{String(s.stopped_after_moving_up)}</Stat>
            <Stat label="…never moved our way">{String(s.stopped_never_moved_up)}</Stat>
            <Stat label="Stop moved to cost">{String(s.stop_moved_to_cost)}</Stat>
            <Stat label="Avg peak (pts)">{pts(s.avg_peak_points as number | null)}</Stat>
            <Stat label="Profit factor">
              {s.profit_factor == null ? "–" : Number(s.profit_factor).toFixed(2)}
            </Stat>
            <Stat label="Lots × lot size">
              {data.lots} ×{" "}
              {Object.entries(data.lot_sizes)
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}
            </Stat>
          </div>
          {data.warnings.map((w) => (
            <p key={w} className="text-xs text-warning">
              {w}
            </p>
          ))}

          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-semibold">Day by day</h3>
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr className="border-b border-border">
                    <th className="py-1 pr-3 font-medium">Day</th>
                    <th className="py-1 pr-3 text-right font-medium">Trades</th>
                    <th className="py-1 pr-3 text-right font-medium">Day P&amp;L</th>
                    <th className="py-1 text-right font-medium">Cumulative</th>
                  </tr>
                </thead>
                <tbody>
                  {[...data.daily].reverse().map((d) => (
                    <tr key={String(d.day)} className="border-b border-border/60 tabular-nums">
                      <td className="py-1 pr-3">{String(d.day)}</td>
                      <td className="py-1 pr-3 text-right">{String(d.trades)}</td>
                      <td className={cn("py-1 pr-3 text-right", tone(Number(d.pnl)))}>
                        {inr(Number(d.pnl))}
                      </td>
                      <td className={cn("py-1 text-right", tone(Number(d.cumulative)))}>
                        {inr(Number(d.cumulative))}
                      </td>
                    </tr>
                  ))}
                  {data.daily.length === 0 && (
                    <tr>
                      <td colSpan={4} className="py-2 text-muted">
                        No tip could be bought in this period.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Every tip ({shown.length})</h3>
              <select
                aria-label="Show tips"
                className="h-7 rounded border border-border bg-surface px-1 text-xs"
                value={only}
                onChange={(e) => setOnly(e.target.value as typeof only)}
              >
                <option value="all">All tips</option>
                <option value="traded">Bought only</option>
                <option value="skipped">Not placed / no data</option>
              </select>
            </div>
            <ul className="flex max-h-[40rem] flex-col gap-2 overflow-y-auto">
              {shown.map((t) => (
                <Trade key={t.signal_id} t={t} />
              ))}
            </ul>
          </div>
        </>
      )}
    </Card>
  );
}
