"use client";

import type { ChartCandle, ChartOptions, ChartSnapshot, SmcOverlay } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { StatusPill, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { Layers } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { followChart, upsertCandle, type ChartMessage, type StreamState } from "@/lib/chart-stream";

import { IST_OFFSET_S, SMC_PALETTE, frameColors } from "./chart-theme";
import { SmcPrimitive, type SmcLayers } from "./smc-primitive";

export interface ChartSpec {
  id: string;
  key: string;
  timeframe: number;
}

type Layer = keyof SmcLayers | "levels";
const LAYERS: { id: Layer; label: string; hint: string }[] = [
  { id: "ob", label: "Order blocks", hint: "Last opposing candle before a break of structure" },
  { id: "fvg", label: "FVG", hint: "Fair value gaps" },
  { id: "structure", label: "BOS / CHoCH", hint: "Breaks of structure and changes of character" },
  { id: "liquidity", label: "Liquidity", hint: "Equal highs and lows, until swept" },
  { id: "levels", label: "Key levels", hint: "Previous day high/low and the nearest swing high/low" },
];
const ALL_ON: Record<Layer, boolean> = {
  ob: true,
  fvg: true,
  structure: true,
  liquidity: true,
  levels: true,
};

const toBar = (c: ChartCandle): CandlestickData<UTCTimestamp> => ({
  time: (c.time + IST_OFFSET_S) as UTCTimestamp,
  open: c.open,
  high: c.high,
  low: c.low,
  close: c.close,
});

function statusPill(state: StreamState, status: ChartSnapshot["status"] | null, detail: string | null) {
  if (state === "failed") return <StatusPill tone="danger">{detail ?? "Unavailable"}</StatusPill>;
  if (state !== "open")
    return <StatusPill tone="neutral">{state === "retrying" ? "Reconnecting" : "Connecting"}</StatusPill>;
  if (status === "live") return <StatusPill tone="success">Live</StatusPill>;
  if (status === "simulated") return <StatusPill tone="warning">Simulated</StatusPill>;
  return <StatusPill tone="neutral">No live prices</StatusPill>;
}

/** The overlays a chart draws, switched on and off from a small menu so the chart keeps its height. */
function OverlayMenu({
  layers,
  onToggle,
}: {
  layers: Record<Layer, boolean>;
  onToggle: (id: Layer) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  const on = LAYERS.filter((l) => layers[l.id]).length;
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
        className="flex h-7 items-center gap-1.5 rounded-lg border border-border px-2 text-xs font-medium text-muted hover:text-foreground"
      >
        <Layers className="size-3.5" aria-hidden />
        SMC
        <span className="tabular-nums">
          {on}/{LAYERS.length}
        </span>
      </button>
      {open && (
        <div
          role="group"
          aria-label="Overlays"
          className="absolute right-0 z-20 mt-1 w-60 rounded-lg border border-border bg-surface p-1 shadow-card"
        >
          {LAYERS.map((l) => (
            <label
              key={l.id}
              className="flex items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-2"
            >
              <input
                type="checkbox"
                checked={layers[l.id]}
                onChange={() => onToggle(l.id)}
                className="mt-0.5 accent-[var(--primary)]"
              />
              <span>
                <span className="block font-medium">{l.label}</span>
                <span className="block text-xs text-muted">{l.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** One live candlestick chart with SMC zones, its own index and timeframe picker. */
export function SmcChart({
  spec,
  options,
  onChange,
}: {
  spec: ChartSpec;
  options: ChartOptions;
  onChange: (spec: ChartSpec) => void;
}) {
  const { getToken } = useAuth();
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const primitive = useRef<SmcPrimitive | null>(null);
  const candles = useRef<ChartCandle[]>([]);
  const priceLines = useRef<IPriceLine[]>([]);
  const overlayRef = useRef<SmcOverlay | null>(null);
  const [layers, setLayers] = useState(ALL_ON);
  const layersRef = useRef(layers);
  const [state, setState] = useState<StreamState>("connecting");
  const [detail, setDetail] = useState<string | null>(null);
  const [status, setStatus] = useState<ChartSnapshot["status"] | null>(null);
  const [last, setLast] = useState<{ price: number; change: number | null } | null>(null);
  const [empty, setEmpty] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("dark");

  // the chart itself: created once, sized to its box
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 6, borderVisible: false },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      localization: { locale: "en-IN", priceFormatter: (p: number) => formatNumber(p) },
    });
    const s = c.addSeries(CandlestickSeries, { borderVisible: false, priceLineStyle: LineStyle.Dotted });
    const p = new SmcPrimitive(SMC_PALETTE.dark);
    s.attachPrimitive(p);
    chart.current = c;
    series.current = s;
    primitive.current = p;
    return () => {
      c.remove();
      chart.current = series.current = primitive.current = null;
      priceLines.current = [];
    };
  }, []);

  // follow the theme class on <html> (set by next-themes), after it has changed, so the tokens read are current
  useEffect(() => {
    const html = document.documentElement;
    const read = () => setTheme(html.classList.contains("dark") ? "dark" : "light");
    read();
    const obs = new MutationObserver(read);
    obs.observe(html, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);

  // light / dark
  useEffect(() => {
    const f = frameColors();
    chart.current?.applyOptions({
      layout: {
        background: { type: ColorType.Solid, color: f.background },
        textColor: f.text,
        fontFamily: "var(--font-inter), ui-sans-serif, system-ui, sans-serif",
        attributionLogo: false,
      },
      grid: { vertLines: { color: f.grid }, horzLines: { color: f.grid } },
      crosshair: {
        vertLine: { labelBackgroundColor: f.border },
        horzLine: { labelBackgroundColor: f.border },
      },
    });
    series.current?.applyOptions({
      upColor: f.up,
      downColor: f.down,
      wickUpColor: f.up,
      wickDownColor: f.down,
    });
    primitive.current?.setPalette(SMC_PALETTE[theme]);
    drawLevels();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- drawLevels reads refs only
  }, [theme]);

  function drawLevels() {
    const s = series.current;
    if (!s) return;
    for (const l of priceLines.current) s.removePriceLine(l);
    priceLines.current = [];
    const overlay = overlayRef.current;
    if (!overlay || !layersRef.current.levels) return;
    const f = frameColors();
    const pal = SMC_PALETTE[theme];
    for (const lv of overlay.levels) {
      const color = lv.kind === "support" ? pal.bos.bull : lv.kind === "resistance" ? pal.bos.bear : f.text;
      priceLines.current.push(
        s.createPriceLine({
          price: lv.price,
          color,
          lineWidth: 1,
          lineStyle: lv.kind === "pdh" || lv.kind === "pdl" ? LineStyle.LargeDashed : LineStyle.SparseDotted,
          axisLabelVisible: true,
          title: lv.label,
        }),
      );
    }
  }

  function setOverlay(o: SmcOverlay) {
    overlayRef.current = o;
    primitive.current?.setOverlay(o);
    drawLevels();
  }

  function priceInfo(close: number) {
    const list = candles.current;
    const today = new Date((list[list.length - 1]?.time ?? 0) * 1000 + IST_OFFSET_S * 1000).getUTCDate();
    const prevDay = [...list]
      .reverse()
      .find((c) => new Date((c.time + IST_OFFSET_S) * 1000).getUTCDate() !== today);
    setLast({ price: close, change: prevDay ? ((close - prevDay.close) / prevDay.close) * 100 : null });
  }

  // the live stream for the chosen index and timeframe
  useEffect(() => {
    const ctrl = new AbortController();
    candles.current = [];
    series.current?.setData([]);
    setOverlay({ boxes: [], lines: [], levels: [], candles: 0, swing_length: 0 });
    let first = true;

    const onMessage = (m: ChartMessage) => {
      const s = series.current;
      // a message still in flight from the previous index or timeframe must never land on this one's chart
      if (!s || ctrl.signal.aborted) return;
      switch (m.type) {
        case "snapshot": {
          candles.current = m.forming ? [...m.candles, m.forming] : [...m.candles];
          s.setData(candles.current.map(toBar));
          if (first) {
            // a new index or timeframe: fit the price axis to its prices again (it may have been dragged or
            // zoomed on the previous one) and show the latest candles
            s.priceScale().applyOptions({ autoScale: true });
            chart.current?.timeScale().resetTimeScale();
            chart.current?.timeScale().scrollToRealTime();
            first = false;
          }
          setOverlay(m.smc);
          setStatus(m.status);
          setEmpty(candles.current.length === 0);
          const lc = candles.current[candles.current.length - 1];
          if (lc) priceInfo(m.last_price ?? lc.close);
          break;
        }
        case "candle":
        case "revise": {
          const { list, atEnd } = upsertCandle(candles.current, m.candle);
          candles.current = list;
          if (atEnd && m.type === "candle") s.update(toBar(m.candle));
          else s.setData(list.map(toBar)); // an older candle changed: redraw (update() only moves the last one)
          setEmpty(false);
          priceInfo(list[list.length - 1]?.close ?? m.candle.close);
          break;
        }
        case "smc":
          setOverlay(m.smc);
          break;
        case "status":
          setStatus(m.status);
          break;
        case "reconnect":
          break;
      }
    };

    void followChart({
      chartKey: spec.key,
      timeframe: spec.timeframe,
      getToken,
      onMessage,
      onState: (st, d) => {
        setState(st);
        setDetail(d ?? null);
      },
      signal: ctrl.signal,
    });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one stream per index + timeframe
  }, [spec.key, spec.timeframe, getToken]);

  function select(next: ChartSpec) {
    setLast(null);
    setStatus(null);
    setDetail(null);
    setState("connecting");
    onChange(next);
  }

  function toggle(id: Layer) {
    const next = { ...layers, [id]: !layers[id] };
    setLayers(next);
    layersRef.current = next;
    primitive.current?.setLayers({
      fvg: next.fvg,
      ob: next.ob,
      structure: next.structure,
      liquidity: next.liquidity,
    });
    drawLevels();
  }

  const name = options.instruments.find((i) => i.code === spec.key)?.name ?? spec.key;
  const tone = last?.change == null ? "text-muted" : last.change >= 0 ? "text-profit" : "text-loss";

  return (
    <section
      aria-label={`${name} ${spec.timeframe} minute chart`}
      className="flex h-full min-w-0 flex-col rounded-card border border-border bg-surface shadow-card"
    >
      <div className="@container flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-2.5 py-1.5">
        <label className="sr-only" htmlFor={`${spec.id}-index`}>
          Index
        </label>
        <select
          id={`${spec.id}-index`}
          value={spec.key}
          onChange={(e) => select({ ...spec, key: e.target.value })}
          className="h-8 rounded-lg border border-border bg-surface-2 px-2 text-sm font-medium"
        >
          {options.instruments.map((i) => (
            <option key={i.code} value={i.code}>
              {i.name}
            </option>
          ))}
        </select>
        <div role="radiogroup" aria-label="Timeframe" className="flex rounded-lg bg-surface-2 p-0.5">
          {options.timeframes.map((tf) => (
            <button
              key={tf}
              type="button"
              role="radio"
              aria-checked={spec.timeframe === tf}
              onClick={() => select({ ...spec, timeframe: tf })}
              className={cn(
                "h-7 rounded-md px-2.5 text-xs font-medium tabular-nums",
                spec.timeframe === tf
                  ? "bg-surface text-foreground shadow-card"
                  : "text-muted hover:text-foreground",
              )}
            >
              {tf}m
            </button>
          ))}
        </div>
        {last && (
          <span className="text-sm whitespace-nowrap tabular-nums">
            <span className="font-semibold">{formatNumber(last.price)}</span>
            {last.change != null && (
              <span className={cn("ml-1.5 hidden text-xs @md:inline", tone)}>
                {last.change >= 0 ? "+" : ""}
                {last.change.toFixed(2)}%
              </span>
            )}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          <OverlayMenu layers={layers} onToggle={toggle} />
          {statusPill(state, status, detail)}
        </span>
      </div>
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-b-card">
        <div ref={box} className="absolute inset-0" />
        {empty && state === "open" && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-muted">
            No candles yet for {name}. They appear as soon as the price feed sends them.
          </p>
        )}
      </div>
    </section>
  );
}
