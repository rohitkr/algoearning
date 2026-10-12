import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DaywiseBreakdown, dayStrengths, monthBlocks } from "./daywise";

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

  it("renders a card per month with its total and a square per traded day", () => {
    render(<DaywiseBreakdown daily={daily} />);
    const apr = screen.getByRole("region", { name: "Apr 2026" });
    expect(within(apr).getByText(/15,002/)).toBeTruthy();
    expect(within(apr).getByLabelText(/Mon, 13 Apr 2026: −4,998.5 \(2 trades\)/)).toBeTruthy();
    expect(within(apr).getByLabelText(/Tue, 14 Apr 2026: \+20,000.5/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "May 2026" })).toBeTruthy();
    expect(screen.queryByRole("img", { name: /one square per day/ })).toBeNull(); // no year heatmap on top
  });

  it("shows the day and its result in a tip while the pointer is on a square", () => {
    render(<DaywiseBreakdown daily={daily} />);
    const sq = within(screen.getByRole("region", { name: "Apr 2026" })).getByLabelText(/Mon, 13 Apr 2026/);
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.mouseEnter(sq);
    expect(screen.getByRole("status").textContent).toBe("13 Apr 2026 • -₹4,998.50");
    fireEvent.mouseLeave(sq);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("colours each day by its size within its month: faintest for the smallest, full for the biggest", () => {
    const [m] = monthBlocks([
      { day: "2026-04-01", pnl: 1000, trades: 2, cumulative: 0 },
      { day: "2026-04-02", pnl: 5000, trades: 2, cumulative: 0 },
      { day: "2026-04-03", pnl: 3000, trades: 2, cumulative: 0 },
      { day: "2026-04-06", pnl: -200, trades: 2, cumulative: 0 },
      { day: "2026-04-07", pnl: -600, trades: 2, cumulative: 0 },
      { day: "2026-04-08", pnl: -400, trades: 2, cumulative: 0 },
    ]);
    const s = dayStrengths(m!);
    expect([...s.entries()]).toEqual([
      ["2026-04-01", 30], // smallest profit
      ["2026-04-02", 100], // biggest profit
      ["2026-04-03", 65],
      ["2026-04-06", 30], // smallest loss
      ["2026-04-07", 100], // worst loss
      ["2026-04-08", 65],
    ]);
  });

  it("shows a lone profit and a lone loss at full colour", () => {
    const [m] = monthBlocks([
      { day: "2026-04-01", pnl: 10, trades: 1, cumulative: 0 },
      { day: "2026-04-02", pnl: -10, trades: 1, cumulative: 0 },
    ]);
    expect([...dayStrengths(m!).values()]).toEqual([100, 100]);
  });

  it("shows nothing for a result with no trades", () => {
    const { container } = render(<DaywiseBreakdown daily={[]} />);
    expect(container.textContent).toBe("");
  });
});
