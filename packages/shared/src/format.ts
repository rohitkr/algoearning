// Number formatting used everywhere a price or P&L is shown (Indian grouping: 1,23,456.78).

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

export const DASH = "–";

export function formatInr(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? DASH : inr.format(v);
}

export function formatNumber(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? DASH : num.format(v);
}

export type PnlTone = "profit" | "loss" | "flat";

/** Colour tone of a P&L value: the UI maps it to its profit/loss theme tokens. */
export function pnlTone(v: number | null | undefined): PnlTone {
  if (v == null || Number.isNaN(v) || v === 0) return "flat";
  return v > 0 ? "profit" : "loss";
}

/** Signed P&L text, e.g. "+₹1,250" / "-₹300.5" / "₹0". */
export function formatPnl(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return DASH;
  if (v === 0) return inr.format(0);
  return (v > 0 ? "+" : "-") + inr.format(Math.abs(v));
}

export function formatPercent(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return DASH;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}
