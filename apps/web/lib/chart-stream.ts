"use client";

import type { ChartCandle, ChartSnapshot, SmcOverlay } from "@algoearning/api-types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** What the chart stream sends (apps/api/src/ae_api/charts.py), one JSON message per server-sent event. */
export type ChartMessage =
  | ChartSnapshot
  | { type: "candle"; candle: ChartCandle; closed: boolean }
  | { type: "revise"; candle: ChartCandle }
  | { type: "smc"; smc: SmcOverlay }
  | { type: "status"; status: ChartSnapshot["status"] }
  | { type: "reconnect" };

export type StreamState = "connecting" | "open" | "retrying" | "failed";

/** Split a server-sent-events buffer into complete events' data, and what is left for the next chunk. Comments
 * (": ping") and other fields are skipped. */
export function parseSse(buffer: string): { data: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const data: string[] = [];
  for (const block of blocks) {
    const lines = block.split("\n").filter((l) => l.startsWith("data:"));
    if (lines.length) data.push(lines.map((l) => l.slice(5).replace(/^ /, "")).join("\n"));
  }
  return { data, rest };
}

/** Put a candle into a time-ordered list: replace the one with the same time, else insert in order. Returns the
 * list and whether the change was at the end (the chart can then .update() instead of redrawing everything). */
export function upsertCandle(list: ChartCandle[], c: ChartCandle): { list: ChartCandle[]; atEnd: boolean } {
  const last = list[list.length - 1];
  if (!last || c.time > last.time) return { list: [...list, c], atEnd: true };
  if (c.time === last.time) return { list: [...list.slice(0, -1), c], atEnd: true };
  const i = list.findIndex((x) => x.time >= c.time);
  const next = [...list];
  if (next[i]?.time === c.time) next[i] = c;
  else next.splice(i, 0, c);
  return { list: next, atEnd: false };
}

const BACKOFF_MS = [1000, 2000, 5000, 10000, 20000];

/** Follow one chart's stream until `signal` aborts, reconnecting with a fresh token after every drop (the server
 * also ends each stream after a while on purpose). Every (re)connection starts with a snapshot, so the chart is
 * always rebuilt from the server's state and never from a guess about what was missed. */
export async function followChart(opts: {
  chartKey: string;
  timeframe: number;
  getToken: () => Promise<string | null>;
  onMessage: (m: ChartMessage) => void;
  onState: (s: StreamState, detail?: string) => void;
  signal: AbortSignal;
}): Promise<void> {
  const { chartKey, timeframe, getToken, onMessage, onState, signal } = opts;
  const url = `${BASE}/v1/charts/stream?key=${encodeURIComponent(chartKey)}&timeframe=${timeframe}`;
  let failures = 0;
  while (!signal.aborted) {
    onState(failures ? "retrying" : "connecting");
    try {
      const token = await getToken();
      const r = await fetch(url, {
        headers: { Accept: "text/event-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        cache: "no-store",
        signal,
      });
      if (r.status === 400 || r.status === 403) {
        const body = (await r.json().catch(() => null)) as { error?: { message?: string } } | null;
        onState("failed", body?.error?.message ?? `request refused (${r.status})`);
        return;
      }
      if (!r.ok || !r.body) throw new Error(`stream failed (${r.status})`);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { data, rest } = parseSse(buffer);
        buffer = rest;
        for (const d of data) {
          if (signal.aborted) return;
          const msg = JSON.parse(d) as ChartMessage;
          if (msg.type === "snapshot") {
            failures = 0;
            onState("open");
          }
          onMessage(msg);
        }
      }
    } catch (e) {
      if (signal.aborted) return;
      failures += 1;
      onState("retrying", (e as Error).message);
    }
    if (signal.aborted) return;
    const wait = failures ? (BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)] ?? 20000) : 300;
    await new Promise((res) => setTimeout(res, wait));
  }
}
