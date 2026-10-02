"use client";

import type { ChartOptions } from "@algoearning/api-types";
import { cn } from "@algoearning/ui";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type PointerEvent as ReactPointerEvent,
} from "react";

import {
  ALL_ON,
  Dropdown,
  LAYERS,
  OverlayMenu,
  TimeframePicker,
  type Layer,
  type LayerSet,
} from "./controls";
import {
  LAYOUTS,
  MAX_CHARTS,
  equal,
  layoutById,
  placement,
  resizeTracks,
  template,
  type Layout,
  type LayoutId,
} from "./layouts";
import { SmcChart, type ChartSpec } from "./smc-chart";

const STORAGE_KEY = "ae.charts.v2";
const GUTTER = 8; // px between charts; drag it to resize
const MIN_BOARD = 420; // px: the board never gets shorter than this, the page scrolls instead
const PHONE = "(max-width: 767px)"; // phones stack the charts, one under the other, at a fixed height

export interface BoardState {
  layout: LayoutId;
  charts: ChartSpec[]; // always MAX_CHARTS long: a layout shows the first n, the rest wait for a bigger layout
  cols: number[];
  rows: number[];
}

function defaults(options: ChartOptions): BoardState {
  const codes = options.instruments.map((i) => i.code);
  const tf = options.timeframes.includes(5) ? 5 : (options.timeframes[0] ?? 5);
  const charts = Array.from({ length: MAX_CHARTS }, (_, n) => ({
    id: `c${n}`,
    key: codes[n % Math.max(codes.length, 1)] ?? "NIFTY",
    timeframe: tf,
    layers: { ...ALL_ON },
  }));
  return { layout: "2c", charts, cols: equal(2), rows: equal(1) };
}

/** Saved overlay switches, or all on when they are missing or from an older version. */
function pickLayers(v: unknown): LayerSet {
  const r = (v && typeof v === "object" ? v : {}) as Record<string, unknown>;
  if (!LAYERS.every((l) => typeof r[l.id] === "boolean")) return { ...ALL_ON };
  return Object.fromEntries(LAYERS.map((l) => [l.id, r[l.id]])) as LayerSet;
}

const validSizes = (v: unknown, n: number): v is number[] =>
  Array.isArray(v) && v.length === n && v.every((x) => typeof x === "number" && x > 0 && Number.isFinite(x));

/** The saved board, with anything no longer possible replaced by a default (an index switched off, a timeframe
 * removed, a layout renamed). */
export function sanitize(saved: unknown, options: ChartOptions): BoardState {
  const base = defaults(options);
  if (!saved || typeof saved !== "object") return base;
  const s = saved as Partial<BoardState>;
  const layout = layoutById(s.layout) ?? (layoutById(base.layout) as Layout);
  const codes = new Set(options.instruments.map((i) => i.code));
  const charts = base.charts.map((d, n) => {
    const c = Array.isArray(s.charts) ? (s.charts[n] as Partial<ChartSpec> | undefined) : undefined;
    return c &&
      typeof c.id === "string" &&
      typeof c.key === "string" &&
      codes.has(c.key) &&
      typeof c.timeframe === "number" &&
      options.timeframes.includes(c.timeframe)
      ? {
          id: c.id,
          key: c.key,
          timeframe: c.timeframe,
          layers: pickLayers(c.layers),
        }
      : d;
  });
  return {
    layout: layout.id,
    charts,
    cols: validSizes(s.cols, layout.cols) ? s.cols : equal(layout.cols),
    rows: validSizes(s.rows, layout.rows) ? s.rows : equal(layout.rows),
  };
}

// The saved board: this browser's localStorage, with an in-memory copy so the page still works when storage is
// blocked. A tiny external store, so the server render and the first client render agree (no saved board yet).
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

function usePhone(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(PHONE);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(PHONE).matches,
    () => false,
  );
}

/** A small picture of a layout for its button. */
function LayoutIcon({ layout }: { layout: Layout }) {
  return (
    <span
      aria-hidden
      className="grid size-5 gap-[2px]"
      style={{
        gridTemplateColumns: `repeat(${layout.cols}, 1fr)`,
        gridTemplateRows: `repeat(${layout.rows}, 1fr)`,
      }}
    >
      {layout.cells.map((c, i) => (
        <span
          key={i}
          className="rounded-[2px] bg-current"
          style={{
            gridColumn: `${c.col + 1} / span ${c.colSpan ?? 1}`,
            gridRow: `${c.row + 1} / span ${c.rowSpan ?? 1}`,
          }}
        />
      ))}
    </span>
  );
}

/** A border between charts that can be dragged to resize them. */
function Gutter({
  axis,
  style,
  onDrag,
  onReset,
}: {
  axis: "col" | "row";
  style: React.CSSProperties;
  onDrag: (deltaShare: number) => void;
  onReset: () => void;
}) {
  const start = useRef<{ pos: number; size: number } | null>(null);
  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    const board = e.currentTarget.parentElement;
    if (!board) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const r = board.getBoundingClientRect();
    start.current = {
      pos: axis === "col" ? e.clientX : e.clientY,
      size: axis === "col" ? r.width : r.height,
    };
    document.body.style.userSelect = "none";
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const s = start.current;
    if (!s) return;
    const pos = axis === "col" ? e.clientX : e.clientY;
    onDrag((pos - s.pos) / s.size);
    s.pos = pos;
  };
  const up = () => {
    start.current = null;
    document.body.style.userSelect = "";
  };
  return (
    <div
      role="separator"
      aria-orientation={axis === "col" ? "vertical" : "horizontal"}
      aria-label="Drag to resize the charts, double-click to make them equal"
      title="Drag to resize, double-click to reset"
      tabIndex={0}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 0.1 : 0.02;
        const back = axis === "col" ? "ArrowLeft" : "ArrowUp";
        const fwd = axis === "col" ? "ArrowRight" : "ArrowDown";
        if (e.key === back || e.key === fwd) {
          e.preventDefault();
          onDrag(e.key === fwd ? step : -step);
        }
      }}
      style={style}
      className={cn(
        "group relative z-10 flex touch-none items-center justify-center",
        axis === "col" ? "cursor-col-resize" : "cursor-row-resize",
      )}
    >
      <span
        className={cn(
          "rounded-full bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary group-active:bg-primary",
          axis === "col" ? "h-10 w-1" : "h-1 w-10",
        )}
      />
    </div>
  );
}

/** The charts page: a TradingView-style layout of 1 to 4 live SMC charts, each with its own index and timeframe,
 * resizable by dragging the borders between them. The board fills the window below it; the choice and the sizes
 * are remembered in this browser. */
export function ChartBoard({ options }: { options: ChartOptions }) {
  const raw = useSyncExternalStore(subscribe, readSaved, () => null);
  const board = useMemo(() => sanitize(parse(raw), options), [raw, options]);
  const layout = layoutById(board.layout) as Layout;
  const save = (next: BoardState) => writeSaved(JSON.stringify(next));
  const phone = usePhone();

  // the board fills the window from where it starts down to the bottom (minus the page's bottom padding)
  const boxRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const fit = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      const main = el.closest("main");
      const pad = main ? parseFloat(getComputedStyle(main).paddingBottom) || 0 : 0;
      setHeight(Math.max(MIN_BOARD, Math.floor(window.innerHeight - top - pad)));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  // keep the live drag smooth: sizes change in state, and are saved once the drag settles
  const [live, setLive] = useState<{ cols: number[]; rows: number[] } | null>(null);
  const sizes = live ?? { cols: board.cols, rows: board.rows };
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => void (saveTimer.current && clearTimeout(saveTimer.current)), []);
  function resize(axis: "cols" | "rows", i: number, delta: number) {
    const next = { ...sizes, [axis]: resizeTracks(sizes[axis], i, delta) };
    setLive(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      save({ ...board, ...next });
      setLive(null);
    }, 250);
  }
  function resetSizes() {
    setLive(null);
    save({ ...board, cols: equal(layout.cols), rows: equal(layout.rows) });
  }

  function pickLayout(l: Layout) {
    setLive(null);
    save({ ...board, layout: l.id, cols: equal(l.cols), rows: equal(l.rows) });
  }

  if (options.instruments.length === 0) {
    return <p className="text-sm text-muted">No index can be charted right now.</p>;
  }

  const shown = board.charts.slice(0, layout.cells.length);
  const update = (n: number) => (spec: ChartSpec) =>
    save({ ...board, charts: board.charts.map((c, i) => (i === n ? spec : c)) });

  // the toolbar sets every chart shown at once; each chart's own controls still change just that chart, and the
  // toolbar then shows the charts disagree (no timeframe highlighted, a half-ticked overlay)
  const setAll = (f: (c: ChartSpec) => ChartSpec) =>
    save({ ...board, charts: board.charts.map((c, i) => (i < shown.length ? f(c) : c)) });
  const commonTf = shown.every((c) => c.timeframe === shown[0]?.timeframe)
    ? (shown[0]?.timeframe ?? null)
    : null;
  const commonLayers = Object.fromEntries(
    LAYERS.map((l) => {
      const on = shown.filter((c) => c.layers[l.id]).length;
      return [l.id, on === shown.length ? true : on === 0 ? false : "mixed"];
    }),
  ) as Record<Layer, boolean | "mixed">;

  // the borders that can be dragged: one per pair of neighbouring tracks, cut where a chart spans across it
  const gutters: React.ReactNode[] = [];
  if (!phone) {
    const covers = (track: "col" | "row", boundary: number, other: number) =>
      layout.cells.some((c) => {
        const [start, span, o, oSpan] =
          track === "col"
            ? [c.col, c.colSpan ?? 1, c.row, c.rowSpan ?? 1]
            : [c.row, c.rowSpan ?? 1, c.col, c.colSpan ?? 1];
        return start <= boundary && start + span > boundary + 1 && o <= other && o + oSpan > other;
      });
    for (let b = 0; b < layout.cols - 1; b++)
      for (let r = 0; r < layout.rows; r++)
        if (!covers("col", b, r))
          gutters.push(
            <Gutter
              key={`c${b}-${r}`}
              axis="col"
              style={{ gridColumn: `${2 * b + 2}`, gridRow: `${2 * r + 1}` }}
              onDrag={(d) => resize("cols", b, d)}
              onReset={resetSizes}
            />,
          );
    for (let b = 0; b < layout.rows - 1; b++)
      for (let c = 0; c < layout.cols; c++)
        if (!covers("row", b, c))
          gutters.push(
            <Gutter
              key={`r${b}-${c}`}
              axis="row"
              style={{ gridRow: `${2 * b + 2}`, gridColumn: `${2 * c + 1}` }}
              onDrag={(d) => resize("rows", b, d)}
              onReset={resetSizes}
            />,
          );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">All charts</span>
        <TimeframePicker
          label="Timeframe for all charts"
          timeframes={options.timeframes}
          value={commonTf}
          onChange={(tf) => setAll((c) => ({ ...c, timeframe: tf }))}
        />
        <OverlayMenu
          title="SMC overlays for all charts"
          layers={commonLayers}
          onToggle={(id, on) => setAll((c) => ({ ...c, layers: { ...c.layers, [id]: on } }))}
        />
        <span className="ml-auto text-xs text-muted">Layout</span>
        <Dropdown label={`Chart layout: ${layout.label}`} button={<LayoutIcon layout={layout} />}>
          {(close) => (
            <div role="radiogroup" aria-label="Chart layout" className="w-64">
              {LAYOUTS.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="radio"
                  aria-checked={board.layout === l.id}
                  onClick={() => {
                    pickLayout(l);
                    close();
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-2",
                    board.layout === l.id ? "bg-surface-2 text-primary-text" : "text-foreground",
                  )}
                >
                  <LayoutIcon layout={l} />
                  {l.label}
                </button>
              ))}
            </div>
          )}
        </Dropdown>
      </div>
      <div
        ref={boxRef}
        className={phone ? "flex flex-col gap-4" : "grid"}
        style={
          phone
            ? undefined
            : {
                height: height ?? `calc(100dvh - 12rem)`,
                gridTemplateColumns: template(sizes.cols, GUTTER),
                gridTemplateRows: template(sizes.rows, GUTTER),
              }
        }
      >
        {shown.map((c, n) => (
          <div
            key={c.id}
            className={phone ? "h-[480px]" : "min-h-0 min-w-0"}
            style={phone ? undefined : placement(layout.cells[n] ?? { col: 0, row: 0 })}
          >
            <SmcChart spec={c} options={options} onChange={update(n)} />
          </div>
        ))}
        {gutters}
      </div>
    </div>
  );
}
