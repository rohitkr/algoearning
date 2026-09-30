"use client";

import type { ReportDay } from "@algoearning/api-types";
import { formatPnl } from "@algoearning/shared";
import { useEffect, useRef, useState } from "react";

const H = 180;
const PAD = { top: 10, right: 8, bottom: 22, left: 8 };
const day = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });
const fmtDay = (d: string) => day.format(new Date(`${d}T00:00:00`));

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => e && setW(Math.max(240, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

function Tip({ x, w, children }: { x: number; w: number; children: React.ReactNode }) {
  const left = Math.min(Math.max(x - 80, 0), w - 160);
  return (
    <div
      role="status"
      className="pointer-events-none absolute top-0 w-40 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs shadow-card"
      style={{ left }}
    >
      {children}
    </div>
  );
}

/** Daily P&L: bars up from zero for profit, down for loss. Loss bars are also hatched, so sign never depends on
 * telling green from red (position is the primary encoding, colour and texture back it up). */
export function DailyPnlBars({ days }: { days: ReportDay[] }) {
  const { ref, w } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...days.map((d) => Math.abs(d.pnl)));
  const hasNeg = days.some((d) => d.pnl < 0);
  const hasPos = days.some((d) => d.pnl > 0);
  const innerH = H - PAD.top - PAD.bottom;
  const zeroY = PAD.top + (hasPos && hasNeg ? innerH / 2 : hasNeg ? 0 : innerH);
  const scale = (hasPos && hasNeg ? innerH / 2 : innerH) / max;
  const slot = (w - PAD.left - PAD.right) / Math.max(1, days.length);
  const bw = Math.max(2, Math.min(28, slot - 2)); // 2px surface gap between bars
  const h = hover == null ? null : days[hover];
  return (
    <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
      <svg width={w} height={H} role="img" aria-label="Daily P&L bar chart">
        <defs>
          <pattern
            id="loss-hatch"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="6" height="6" className="fill-loss" />
            <line x1="0" y1="0" x2="0" y2="6" className="stroke-surface" strokeWidth="2" />
          </pattern>
        </defs>
        <line x1={PAD.left} x2={w - PAD.right} y1={zeroY} y2={zeroY} className="stroke-border" />
        {days.map((d, i) => {
          const x = PAD.left + i * slot + (slot - bw) / 2;
          const bh = Math.max(1, Math.abs(d.pnl) * scale);
          const y = d.pnl >= 0 ? zeroY - bh : zeroY;
          return (
            <g key={d.day}>
              <rect
                x={x}
                y={y}
                width={bw}
                height={bh}
                rx={Math.min(4, bw / 2)}
                className={d.pnl >= 0 ? "fill-profit" : undefined}
                fill={d.pnl < 0 ? "url(#loss-hatch)" : undefined}
                opacity={hover == null || hover === i ? 1 : 0.55}
              />
              {/* hit target: the whole column, wider than the bar */}
              <rect
                x={PAD.left + i * slot}
                y={0}
                width={slot}
                height={H}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                tabIndex={0}
                aria-label={`${fmtDay(d.day)}: ${formatPnl(d.pnl)}, ${d.trades} trades`}
              />
            </g>
          );
        })}
        {days.length > 0 && (
          <>
            <text x={PAD.left} y={H - 6} className="fill-muted text-[10px]">
              {fmtDay(days[0]!.day)}
            </text>
            <text x={w - PAD.right} y={H - 6} textAnchor="end" className="fill-muted text-[10px]">
              {fmtDay(days[days.length - 1]!.day)}
            </text>
          </>
        )}
      </svg>
      {h && hover != null && (
        <Tip x={PAD.left + hover * slot + slot / 2} w={w}>
          <p className="text-muted">{fmtDay(h.day)}</p>
          <p className="font-semibold tabular-nums">{formatPnl(h.pnl)}</p>
          <p className="text-muted">
            {h.trades} trade{h.trades === 1 ? "" : "s"}
          </p>
        </Tip>
      )}
    </div>
  );
}

/** Cumulative P&L (equity curve), with a crosshair and tooltip on hover. One series: the title names it. */
export function EquityCurve({ days }: { days: ReportDay[] }) {
  const { ref, w } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const pts = [{ day: "", cumulative: 0 }, ...days];
  const vals = pts.map((p) => p.cumulative);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const innerW = w - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (pts.length === 1 ? 0 : (i / (pts.length - 1)) * innerW);
  const y = (v: number) => PAD.top + ((hi - v) / span) * innerH;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(" ");
  const h = hover == null ? null : pts[hover];
  return (
    <div
      ref={ref}
      className="relative"
      onMouseLeave={() => setHover(null)}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        const i = Math.round(((e.clientX - r.left - PAD.left) / innerW) * (pts.length - 1));
        setHover(Math.min(pts.length - 1, Math.max(1, i)));
      }}
    >
      <svg width={w} height={H} role="img" aria-label="Cumulative P&L line chart">
        <line
          x1={PAD.left}
          x2={w - PAD.right}
          y1={y(0)}
          y2={y(0)}
          className="stroke-border"
          strokeDasharray="3 3"
        />
        <path d={d} fill="none" className="stroke-primary" strokeWidth={2} strokeLinejoin="round" />
        {hover != null && h && (
          <>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-muted"
              strokeWidth={1}
            />
            <circle
              cx={x(hover)}
              cy={y(h.cumulative)}
              r={4}
              className="fill-primary stroke-surface"
              strokeWidth={2}
            />
          </>
        )}
      </svg>
      {h && hover != null && hover > 0 && (
        <Tip x={x(hover)} w={w}>
          <p className="text-muted">{fmtDay(h.day)}</p>
          <p className="font-semibold tabular-nums">{formatPnl(h.cumulative)}</p>
          <p className="text-muted">total so far</p>
        </Tip>
      )}
    </div>
  );
}
