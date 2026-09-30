import type { ReactNode } from "react";

import { cn } from "../cn";

export type StatusTone = "success" | "danger" | "warning" | "neutral" | "info";

const TONES: Record<StatusTone, string> = {
  success: "text-profit bg-profit/10",
  danger: "text-loss bg-loss/10",
  warning: "text-warning bg-warning/10",
  info: "text-primary-text bg-primary/10",
  neutral: "text-muted bg-surface-2",
};

/** Small status label (Running / Stopped / Live / Paper / Not connected ...), with a leading dot. */
export function StatusPill({ tone = "neutral", children }: { tone?: StatusTone; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        TONES[tone],
      )}
      data-tone={tone}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}
