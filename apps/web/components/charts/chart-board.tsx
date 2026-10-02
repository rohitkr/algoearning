"use client";

import type { ChartOptions } from "@algoearning/api-types";
import { Button, Tooltip } from "@algoearning/ui";
import { Plus } from "lucide-react";
import { useMemo, useSyncExternalStore } from "react";

import { SmcChart, type ChartSpec } from "./smc-chart";

/** How many charts fit on the page. Raise it to allow more side by side; the layout wraps to new rows. */
export const MAX_CHARTS = 2;
const STORAGE_KEY = "ae.charts.v1";

function defaults(options: ChartOptions): ChartSpec[] {
  const codes = options.instruments.map((i) => i.code);
  const tf = options.timeframes.includes(5) ? 5 : (options.timeframes[0] ?? 5);
  return codes.slice(0, MAX_CHARTS).map((key, n) => ({ id: `c${n}`, key, timeframe: tf }));
}

/** Keep only charts that are still possible (an index can be switched off; the limit can go down). */
export function sanitize(saved: unknown, options: ChartOptions): ChartSpec[] | null {
  if (!Array.isArray(saved)) return null;
  const codes = new Set(options.instruments.map((i) => i.code));
  const out = (saved as Partial<ChartSpec>[])
    .filter(
      (s): s is ChartSpec =>
        typeof s?.id === "string" &&
        typeof s.key === "string" &&
        codes.has(s.key) &&
        typeof s.timeframe === "number" &&
        options.timeframes.includes(s.timeframe),
    )
    .slice(0, MAX_CHARTS);
  return out.length ? out : null;
}

// The saved layout: this browser's localStorage, with an in-memory copy so the page still works when storage is
// blocked. A tiny external store, so the server render and the first client render agree (no saved layout yet).
let memory: string | null | undefined;
const listeners = new Set<() => void>();

function readSaved(): string | null {
  if (memory === undefined) {
    try {
      memory = localStorage.getItem(STORAGE_KEY);
    } catch {
      memory = null;
    }
  }
  return memory;
}

function writeSaved(value: string) {
  memory = value;
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // not remembered across visits, still works now
  }
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function parse(raw: string | null): unknown {
  try {
    return JSON.parse(raw ?? "null");
  } catch {
    return null;
  }
}

/** The charts page: up to MAX_CHARTS live SMC charts side by side, each with its own index and timeframe. The
 * choice is remembered in this browser. */
export function ChartBoard({ options }: { options: ChartOptions }) {
  const raw = useSyncExternalStore(subscribe, readSaved, () => null);
  const charts = useMemo(() => sanitize(parse(raw), options) ?? defaults(options), [raw, options]);
  const save = (next: ChartSpec[]) => writeSaved(JSON.stringify(next));

  function add() {
    if (charts.length >= MAX_CHARTS) return;
    const used = new Set(charts.map((c) => c.key));
    const key = options.instruments.find((i) => !used.has(i.code))?.code ?? options.instruments[0]?.code;
    if (!key) return;
    const id = `c${Date.now().toString(36)}`;
    save([...charts, { id, key, timeframe: charts[0]?.timeframe ?? 5 }]);
  }

  if (options.instruments.length === 0) {
    return <p className="text-sm text-muted">No index can be charted right now.</p>;
  }

  const full = charts.length >= MAX_CHARTS;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        {full ? (
          <Tooltip label={`Up to ${MAX_CHARTS} charts on one page for now`}>
            <span tabIndex={0}>
              <Button variant="secondary" size="sm" disabled>
                <Plus className="size-4" aria-hidden /> Add chart
              </Button>
            </span>
          </Tooltip>
        ) : (
          <Button variant="secondary" size="sm" onClick={add}>
            <Plus className="size-4" aria-hidden /> Add chart
          </Button>
        )}
      </div>
      <div className={charts.length > 1 ? "grid gap-4 xl:grid-cols-2" : "grid gap-4"}>
        {charts.map((c) => (
          <SmcChart
            key={c.id}
            spec={c}
            options={options}
            onChange={(next) => save(charts.map((x) => (x.id === c.id ? next : x)))}
            onRemove={charts.length > 1 ? () => save(charts.filter((x) => x.id !== c.id)) : undefined}
          />
        ))}
      </div>
    </div>
  );
}
