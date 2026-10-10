import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BacktestResultView, type BacktestResultData } from "./result-view";

vi.mock("@/components/reports/pnl-charts", () => ({ DailyPnlBars: () => null, EquityCurve: () => null }));

afterEach(cleanup);

const RES: BacktestResultData = {
  summary: { net_pnl: 0, trades: 1, days_replayed: 3 },
  daily: [], trades: [], trades_total: 0, warnings: [],
  day_log: [
    { day: "2026-08-04", weekday: "Tue", options: true, trades: 2, why: [] },
    { day: "2026-08-05", weekday: "Wed", options: false, trades: 0, why: ["no option prices stored for this day"] },
    { day: "2026-08-07", weekday: "Fri", options: true, trades: 0, why: ["this weekday is not ticked under Trade on"] },
  ],
}; // prettier-ignore

describe("BacktestResultView", () => {
  it("lists every replayed day with its prices and the reason it did not trade", () => {
    render(<BacktestResultView res={RES} />);
    expect(screen.getByText(/3 days replayed: traded on 1, no option prices stored on 1/)).toBeTruthy();
    expect(screen.getByText("no option prices stored for this day")).toBeTruthy();
    expect(screen.getByText("this weekday is not ticked under Trade on")).toBeTruthy();
    expect(screen.getByText("Wed, 2026-08-05")).toBeTruthy();
  });
});
