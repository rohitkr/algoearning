"use client";

import { cn } from "@algoearning/ui";
import { ChevronDown, Layers } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { SmcLayers } from "./smc-primitive";

/** Controls shared by each chart's own header and the toolbar that sets every chart at once. */

export type Layer = keyof SmcLayers | "levels";
export type LayerSet = Record<Layer, boolean>;

export const LAYERS: { id: Layer; label: string; hint: string }[] = [
  { id: "ob", label: "Order blocks", hint: "Last opposing candle before a break of structure" },
  { id: "fvg", label: "FVG", hint: "Fair value gaps" },
  { id: "structure", label: "BOS / CHoCH", hint: "Breaks of structure and changes of character" },
  { id: "liquidity", label: "Liquidity", hint: "Equal highs and lows, until swept" },
  { id: "levels", label: "Key levels", hint: "Previous day high/low and the nearest swing high/low" },
];

export const ALL_ON: LayerSet = { ob: true, fvg: true, structure: true, liquidity: true, levels: true };

/** A button that opens a small panel under it; closes on a click outside or Escape. */
export function Dropdown({
  label,
  button,
  align = "right",
  className,
  children,
}: {
  label: string;
  button: ReactNode;
  align?: "left" | "right";
  className?: string;
  children: (close: () => void) => ReactNode;
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
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex h-8 items-center gap-1.5 rounded-lg border border-border px-2 text-xs font-medium text-muted hover:text-foreground",
          open && "text-foreground",
          className,
        )}
      >
        {button}
        <ChevronDown className="size-3.5" aria-hidden />
      </button>
      {open && (
        <div
          className={cn(
            "absolute z-30 mt-1 rounded-lg border border-border bg-surface p-1 shadow-card",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

/** Switch SMC overlays on and off. `layers` values are true / false, or "mixed" when the charts it sets disagree. */
export function OverlayMenu({
  layers,
  onToggle,
  title = "SMC overlays",
  compact = false,
}: {
  layers: Record<Layer, boolean | "mixed">;
  onToggle: (id: Layer, on: boolean) => void;
  title?: string;
  compact?: boolean; // inside a chart's header: the word "SMC" only when the chart is wide
}) {
  const on = LAYERS.filter((l) => layers[l.id] === true).length;
  return (
    <Dropdown
      label={title}
      className="h-7"
      button={
        <>
          <Layers className="size-3.5" aria-hidden />
          <span className={compact ? "hidden @2xl:inline" : undefined}>SMC</span>
          <span className="tabular-nums">
            {on}/{LAYERS.length}
          </span>
        </>
      }
    >
      {() => (
        <div role="group" aria-label={title} className="w-60">
          {LAYERS.map((l) => (
            <label
              key={l.id}
              className="flex items-start gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-2"
            >
              <input
                type="checkbox"
                checked={layers[l.id] === true}
                ref={(el) => {
                  if (el) el.indeterminate = layers[l.id] === "mixed";
                }}
                onChange={() => onToggle(l.id, layers[l.id] !== true)}
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
    </Dropdown>
  );
}

/** Timeframe buttons. `value` null: the charts it sets disagree, nothing is highlighted. */
export function TimeframePicker({
  timeframes,
  value,
  onChange,
  label = "Timeframe",
}: {
  timeframes: number[];
  value: number | null;
  onChange: (tf: number) => void;
  label?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-lg bg-surface-2 p-0.5">
      {timeframes.map((tf) => (
        <button
          key={tf}
          type="button"
          role="radio"
          aria-checked={value === tf}
          onClick={() => onChange(tf)}
          className={cn(
            "h-7 rounded-md px-2.5 text-xs font-medium tabular-nums",
            value === tf ? "bg-surface text-foreground shadow-card" : "text-muted hover:text-foreground",
          )}
        >
          {tf}m
        </button>
      ))}
    </div>
  );
}
