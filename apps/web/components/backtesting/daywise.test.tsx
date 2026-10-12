import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DaywiseBreakdown, monthBlocks } from "./daywise";

afterEach(cleanup);

const daily = [
  { day: "2026-04-13", pnl: -4998.5, trades: 2, cumulative: -4998.5 },
  { day: "2026-04-14", pnl: 20000.5, trades: 2, cumulative: 15002 },
  { day: "2026-05-04", pnl: -1000, trades: 2, cumulative: 14002 },
];

describe("daywise breakdown", () => {
  it("builds a block per month with the net total and the weekday the month starts on", () => {
    const m = monthBlocks(daily);
    expect(m.map((x) => [x.title, x.total, x.traded, x.days.length, x.lead])).toEqual([
      ["Apr 2026", 15002, 2, 30, 3], // 1 Apr 2026 is a Wednesday
      ["May 2026", -1000, 1, 31, 5], // 1 May 2026 is a Friday
    ]);
  });

  it("covers a month with no trades in between", () => {
    const m = monthBlocks([
      { day: "2026-01-05", pnl: 1, trades: 1, cumulative: 1 },
      { day: "2026-03-02", pnl: 2, trades: 1, cumulative: 3 },
    ]);
    expect(m.map((x) => [x.key, x.total])).toEqual([
      ["2026-01", 1],
      ["2026-02", 0],
      ["2026-03", 2],
    ]);
  });

  it("renders a card per month with its total, and a titled square per traded day", () => {
    render(<DaywiseBreakdown daily={daily} />);
    const apr = screen.getByRole("region", { name: "Apr 2026" });
    expect(within(apr).getByText(/15,002/)).toBeTruthy();
    expect(within(apr).getByLabelText(/Mon, 13 Apr 2026: −4,998.5 \(2 trades\)/)).toBeTruthy();
    expect(within(apr).getByLabelText(/Tue, 14 Apr 2026: \+20,000.5/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "May 2026" })).toBeTruthy();
    expect(screen.getByRole("img", { name: /one square per day/ })).toBeTruthy();
  });

  it("shows nothing for a result with no trades", () => {
    const { container } = render(<DaywiseBreakdown daily={[]} />);
    expect(container.textContent).toBe("");
  });
});
