import { describe, expect, it } from "vitest";

import { DASH, formatInr, formatNumber, formatPercent, formatPnl, pnlTone } from "./format";

describe("format", () => {
  it("uses Indian digit grouping", () => {
    expect(formatNumber(1234567.891)).toBe("12,34,567.89");
    expect(formatInr(1234567.5)).toBe("₹12,34,567.50");
  });

  it("signs P&L and picks a tone", () => {
    expect(formatPnl(1250)).toBe("+₹1,250.00");
    expect(formatPnl(-300.5)).toBe("-₹300.50");
    expect(formatPnl(0)).toBe("₹0.00");
    expect([pnlTone(5), pnlTone(-1), pnlTone(0), pnlTone(null)]).toEqual(["profit", "loss", "flat", "flat"]);
  });

  it("shows a dash for missing values", () => {
    expect([formatInr(null), formatNumber(undefined), formatPnl(Number.NaN), formatPercent(null)]).toEqual([
      DASH,
      DASH,
      DASH,
      DASH,
    ]);
    expect(formatPercent(1.234)).toBe("+1.23%");
  });
});
