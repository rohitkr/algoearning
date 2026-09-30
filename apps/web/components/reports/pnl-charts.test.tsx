import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { DailyPnlBars, EquityCurve } from "./pnl-charts";

const DAYS = [
  { day: "2026-10-01", pnl: 800, trades: 2, cumulative: 800 },
  { day: "2026-10-02", pnl: -300, trades: 1, cumulative: 500 },
];

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
afterEach(cleanup);

describe("P&L charts", () => {
  it("draws profit bars up and loss bars hatched, with a tooltip per day", () => {
    const { container } = render(<DailyPnlBars days={DAYS} />);
    const bars = container.querySelectorAll("rect[rx]");
    expect(bars).toHaveLength(2);
    expect(bars[0]!.getAttribute("class")).toBe("fill-profit");
    expect(bars[1]!.getAttribute("fill")).toBe("url(#loss-hatch)"); // sign is never colour alone
    fireEvent.focus(screen.getByLabelText(/2 Oct: -₹300.*1 trades/));
    expect(screen.getByRole("status").textContent).toContain("-₹300");
  });

  it("draws the cumulative line from zero", () => {
    const { container } = render(<EquityCurve days={DAYS} />);
    const d = container.querySelector("path")!.getAttribute("d")!;
    expect(d.startsWith("M")).toBe(true);
    expect(d.split("L")).toHaveLength(3); // the zero start plus two days
  });
});
