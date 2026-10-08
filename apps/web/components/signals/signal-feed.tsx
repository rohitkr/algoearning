"use client";

import type { SignalKind, SignalMessage, SignalSource, TipSignal } from "@algoearning/api-types";
import { Card, StatusPill, type StatusTone, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";

const POLL_MS = 5000;
const time = new Intl.DateTimeFormat("en-IN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Kolkata",
});
const day = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
const num = (v: number | null | undefined) =>
  v == null ? "–" : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
const ist = (iso: string, withDay = false) => {
  const d = new Date(iso);
  return (withDay ? `${day.format(d)} ` : "") + time.format(d);
};

export const KIND_LABEL: Record<SignalKind, string> = {
  SIGNAL: "Signal",
  DETAILS: "TP / SL",
  TARGET: "Target",
  SL_HIT: "SL hit",
  TICK: "Price",
  MEDIA: "Image",
  ADVISORY: "Advice",
  NOISE: "Chat",
  UNCLEAR: "Unclear",
};
const KIND_TONE: Record<SignalKind, StatusTone> = {
  SIGNAL: "info",
  DETAILS: "info",
  TARGET: "success",
  SL_HIT: "danger",
  TICK: "neutral",
  MEDIA: "neutral",
  ADVISORY: "warning",
  NOISE: "neutral",
  UNCLEAR: "warning",
};
const STATUS: Record<TipSignal["status"], { tone: StatusTone; text: string }> = {
  OPEN: { tone: "info", text: "Open" },
  T1: { tone: "success", text: "Target 1" },
  T2: { tone: "success", text: "Target 2" },
  T3: { tone: "success", text: "Target 3" },
  SL_HIT: { tone: "danger", text: "SL hit" },
};
const READER: Record<SignalSource["reader_state"], { tone: StatusTone; text: string }> = {
  listening: { tone: "success", text: "● Live" },
  connecting: { tone: "warning", text: "Connecting…" },
  error: { tone: "danger", text: "Error" },
  off: { tone: "neutral", text: "Not reading" },
};

/** One source's signals and its chat as read (ADR 0025): refreshed every few seconds while the page is open. */
export function SignalFeed({ source }: { source: SignalSource }) {
  const { getToken } = useAuth();
  const [signals, setSignals] = useState<TipSignal[] | null>(null);
  const [messages, setMessages] = useState<SignalMessage[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [hideTicks, setHideTicks] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([
        apiRequest<TipSignal[]>("GET", `/v1/signal-sources/${source.id}/signals?days=7`, undefined, getToken),
        apiRequest<SignalMessage[]>(
          "GET",
          `/v1/signal-sources/${source.id}/messages?days=3`,
          undefined,
          getToken,
        ),
      ]);
      setSignals(s);
      setMessages(m);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : (e as Error).message);
    }
  }, [source.id, getToken]);

  useEffect(() => {
    const first = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load]);

  async function correct(m: SignalMessage, kind: SignalKind | "") {
    try {
      const path = `/v1/signal-sources/${source.id}/messages/${m.msg_id}/kind`;
      await (kind
        ? apiRequest("PUT", path, { kind }, getToken)
        : apiRequest("DELETE", path, undefined, getToken));
      await load();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : (e as Error).message);
    }
  }

  const reader = READER[source.reader_state];
  const linked = new Set(signals?.find((s) => s.id === selected)?.message_ids ?? []);
  const shown = messages.filter((m) => !hideTicks || m.kind !== "TICK" || linked.has(m.msg_id));

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">
          {source.chat_title} <span className="text-sm font-normal text-muted">signals</span>
        </h2>
        <div className="flex items-center gap-2 text-xs text-muted">
          {source.last_message_at && <span>last message {ist(source.last_message_at, true)}</span>}
          <StatusPill tone={reader.tone}>{reader.text}</StatusPill>
        </div>
      </div>
      {source.reader_state !== "listening" && (
        <p className="text-sm text-muted">
          {source.reader_detail ??
            (source.reader_state === "off"
              ? "The signal reader is not running for this source yet (it starts within a few seconds of connecting)."
              : "")}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr className="border-b border-border">
              <th className="py-2 pr-3 font-medium">Time</th>
              <th className="py-2 pr-3 font-medium">View</th>
              <th className="py-2 pr-3 font-medium">Tip</th>
              <th className="py-2 pr-3 text-right font-medium">Entry</th>
              <th className="py-2 pr-3 text-right font-medium">SL</th>
              <th className="py-2 pr-3 font-medium">Targets</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 text-right font-medium">Last</th>
            </tr>
          </thead>
          <tbody>
            {signals === null && (
              <tr>
                <td colSpan={8} className="py-4 text-muted">
                  Loading…
                </td>
              </tr>
            )}
            {signals?.length === 0 && (
              <tr>
                <td colSpan={8} className="py-4 text-muted">
                  No signals in the last 7 days.
                </td>
              </tr>
            )}
            {signals?.map((s) => {
              const st = STATUS[s.status];
              return (
                <tr
                  key={s.id}
                  onClick={() => setSelected(selected === s.id ? null : s.id)}
                  className={cn(
                    "cursor-pointer border-b border-border/60 hover:bg-surface-2",
                    selected === s.id && "bg-primary/10",
                  )}
                  aria-selected={selected === s.id}
                >
                  <td className="py-2 pr-3 whitespace-nowrap">{ist(s.date, true)}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={cn("font-semibold", s.direction === "BULLISH" ? "text-profit" : "text-loss")}
                    >
                      {s.direction === "BULLISH" ? "▲ Bullish" : "▼ Bearish"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {s.action} {s.index} {s.strike} {s.option_type}
                    {!s.complete && <span className="ml-1 text-xs text-warning">(no SL yet)</span>}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">
                    {num(s.entry_low)}–{num(s.entry_high)}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{num(s.stop_loss)}</td>
                  <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                    {s.targets.map((t, i) => (
                      <span
                        key={i}
                        className={cn(
                          "mr-1 rounded px-1",
                          s.targets_done.includes(i + 1)
                            ? "bg-profit/15 font-semibold text-profit"
                            : "text-muted",
                        )}
                      >
                        {num(t)}
                      </span>
                    ))}
                  </td>
                  <td className="py-2 pr-3">
                    <StatusPill tone={st.tone}>{st.text}</StatusPill>
                  </td>
                  <td className="py-2 text-right tabular-nums">{num(s.last_price)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Messages (last 3 days)</h3>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={hideTicks} onChange={(e) => setHideTicks(e.target.checked)} />
            Hide price ticks
          </label>
        </div>
        <ul className="flex max-h-[28rem] flex-col gap-1 overflow-y-auto">
          {shown.map((m) => (
            <li
              key={m.msg_id}
              className={cn(
                "flex flex-wrap items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 text-sm",
                linked.has(m.msg_id) && "border-primary/40 bg-primary/10",
              )}
            >
              <span className="w-12 shrink-0 pt-0.5 text-xs text-muted tabular-nums">{ist(m.date)}</span>
              <span className="w-20 shrink-0">
                <StatusPill tone={KIND_TONE[m.kind]}>{KIND_LABEL[m.kind]}</StatusPill>
              </span>
              <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
                {m.text || (m.has_media ? "[image]" : "")}
                {m.edit_date && <span className="ml-1 text-xs text-muted">(edited)</span>}
              </span>
              <select
                aria-label={`Correct how message ${m.msg_id} was read`}
                className="h-7 rounded border border-border bg-surface px-1 text-xs text-muted"
                value={m.overridden ? m.kind : ""}
                onChange={(e) => void correct(m, e.target.value as SignalKind | "")}
              >
                <option value="">{m.overridden ? "Undo correction" : "Correct…"}</option>
                {(Object.keys(KIND_LABEL) as SignalKind[]).map((k) => (
                  <option key={k} value={k}>
                    Read as {KIND_LABEL[k]}
                  </option>
                ))}
              </select>
            </li>
          ))}
          {shown.length === 0 && <li className="text-sm text-muted">No messages in the last 3 days.</li>}
        </ul>
      </div>
    </Card>
  );
}
