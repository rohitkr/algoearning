import { formatPnl, pnlTone } from "@algoearning/shared";

import { cn } from "../cn";

const TONE = { profit: "text-profit", loss: "text-loss", flat: "text-foreground" } as const;

/** A P&L amount: signed, Indian-formatted, profit/loss coloured from the theme, fixed-width digits so a
 * ticking value does not jiggle the layout. */
export function Pnl({ value, className }: { value: number | null | undefined; className?: string }) {
  const tone = pnlTone(value);
  return (
    <span className={cn("whitespace-nowrap tabular-nums", TONE[tone], className)} data-tone={tone}>
      {formatPnl(value)}
    </span>
  );
}
