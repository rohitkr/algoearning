"use client";

import type { RunDetail } from "@algoearning/api-types";
import { Card, CardTitle } from "@algoearning/ui";
import { useEffect, useState } from "react";

import { fmt } from "./run-bits";

type RunEvent = RunDetail["events"][number];

const ist = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }); // yyyy-mm-dd
const RANGE_KEY = "ae_events_range";

function describe(detail: Record<string, unknown>): string {
  const skip = new Set(["trade_id", "setup", "reason_steps"]);
  return Object.entries(detail)
    .filter(([k, v]) => !skip.has(k) && v != null && v !== "")
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

/** The events of the days from..to (yyyy-mm-dd, IST; "" = today). */
export function eventsBetween(
  events: readonly RunEvent[],
  from: string,
  to: string,
  now = new Date(),
): RunEvent[] {
  const today = ist.format(now);
  const a = from || today;
  const b = to || today;
  const lo = a <= b ? a : b;
  const hi = a <= b ? b : a;
  return events.filter((e) => {
    const d = ist.format(new Date(e.ts));
    return d >= lo && d <= hi;
  });
}

/** "What happened": today's events by default, any days on request (the choice is kept for the rest of the day). */
export function EventsLog({ events }: { events: RunEvent[] }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        const saved = JSON.parse(localStorage.getItem(RANGE_KEY) ?? "null") as {
          day: string;
          from: string;
          to: string;
        } | null;
        if (saved && saved.day === ist.format(new Date())) {
          setFrom(saved.from);
          setTo(saved.to);
        }
      } catch {
        /* storage unavailable: today */
      }
      setLoaded(true);
    }, 0);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => {
    if (!loaded) return;
    try {
      localStorage.setItem(RANGE_KEY, JSON.stringify({ day: ist.format(new Date()), from, to }));
    } catch {
      /* not remembered: fine */
    }
  }, [from, to, loaded]);
  const shown = eventsBetween(events, from, to);
  const todayOnly = !from && !to;
  const input = "h-8 rounded border border-border bg-surface px-1.5 text-xs text-foreground";
  return (
    <Card className="p-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4 pb-2">
        <div className="flex items-baseline gap-2">
          <CardTitle>What happened</CardTitle>
          <span className="text-xs text-muted">
            ({shown.length}
            {todayOnly ? " today" : ""})
          </span>
        </div>
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <input
            type="date"
            aria-label="Events from"
            className={input}
            value={from || ist.format(new Date())}
            onChange={(e) => setFrom(e.target.value)}
          />
          to
          <input
            type="date"
            aria-label="Events to"
            className={input}
            value={to || ist.format(new Date())}
            onChange={(e) => setTo(e.target.value)}
          />
          {!todayOnly && (
            <button
              type="button"
              className="text-primary-text underline"
              onClick={() => {
                setFrom("");
                setTo("");
              }}
            >
              Today
            </button>
          )}
        </span>
      </div>
      {shown.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted">
          {todayOnly ? "Nothing yet today." : "Nothing in these days."}
        </p>
      ) : (
        <ul className="max-h-[32rem] divide-y divide-border overflow-y-auto px-5 pb-3 text-sm">
          {shown.map((e) => (
            <li key={e.id} className="flex flex-wrap gap-x-3 gap-y-0.5 py-2">
              <span className="text-xs whitespace-nowrap text-muted tabular-nums">{fmt(e.ts)}</span>
              <span
                className={
                  e.level === "ERROR"
                    ? "font-medium text-loss"
                    : e.level === "WARNING"
                      ? "font-medium text-warning"
                      : "font-medium"
                }
              >
                {e.event.replaceAll("_", " ")}
              </span>
              <span className="min-w-0 text-muted">{describe(e.detail)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
