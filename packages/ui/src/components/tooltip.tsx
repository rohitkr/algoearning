"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/** A short label that appears on hover and on keyboard focus (and is announced to screen readers through the
 * trigger's aria-describedby). Wrap the one element it describes; it needs no provider. */
export function Tooltip({
  label,
  children,
  side = "bottom",
  delay = 250,
}: {
  label: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  /** Milliseconds before it appears. */
  delay?: number;
}) {
  return (
    <TooltipPrimitive.Provider delayDuration={delay} skipDelayDuration={300}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side={side}
            sideOffset={6}
            className="z-50 max-w-64 rounded-md border border-border bg-foreground px-2.5 py-1.5 text-xs font-medium text-background shadow-card"
          >
            {label}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
