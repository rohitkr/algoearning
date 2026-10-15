import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Trade } from "./result-view";
import { TradesByDay, groupByDay } from "./trades-by-day";

afterEach(cleanup);

const t = (day: string, contract: string, net: number, exit = `${day}T10:02:00+05:30`): Trade => ({
  leg: "A",
  contract,
  side: "BUY",
  qty: 650,
  entry_time: `${day}T09:20:00+05:30`,
  entry_price: 17.3,
  exit_time: exit,
  exit_price: 11.85,
  reason: "strategy profit reached",
  gross: net,
  charges: 0,
  net,
});
const sell = (day: string, contract: string, net: number): Trade => ({
  ...t(day, contract, net),
  side: "SELL",
});
const TRADES = [
  sell("2026-09-15", "NIFTY 23600 CE", -3542.5),
  t("2026-09-15", "NIFTY 23400 PE", 9542),
  t("2026-09-16", "NIFTY 23350 CE", 100, "2026-09-17T09:30:00+05:30"),
];

describe("TradesByDay", () => {
  it("groups by the day each trade was entered, with the combined net", () => {
    const g = groupByDay(TRADES);
    expect(g.map((x) => [x.key, x.net, x.trades.length])).toEqual([
      ["2026-09-15", 5999.5, 2],
      ["2026-09-16", 100, 1],
    ]);
    expect(g[0]?.title).toMatch(/^Tue, 15 Sept? 2026$/);
  });

  it("tells sells from buys: a labelled side next to the symbol and a signed quantity, and counts them per day", () => {
    render(<TradesByDay trades={TRADES} total={3} />);
    expect(screen.getByText("1 sell", { exact: false }).textContent).toContain("2 trades · 1 sell · 1 buy");
    const row = screen.getByText("NIFTY 23600 CE").closest("tr")!;
    expect(within(row).getByText("SELL")).toBeTruthy();
    expect(within(row).getByText("−650")).toBeTruthy();
    const buyRow = screen.getByText("NIFTY 23400 PE").closest("tr")!;
    expect(within(buyRow).getByText("BUY")).toBeTruthy();
    expect(within(buyRow).getByText("+650")).toBeTruthy();
  });

  it("shows the days collapsed (the first open) and expands one on click", () => {
    render(<TradesByDay trades={TRADES} total={3} />);
    expect(screen.getByText("NIFTY 23600 CE")).toBeTruthy(); // first day open
    expect(screen.queryByText("NIFTY 23350 CE")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /16 Sep/ }));
    expect(screen.getByText("NIFTY 23350 CE")).toBeTruthy();
    expect(screen.getByText(/\(Thu, 17 Sep/)).toBeTruthy(); // a trade held to another day says which
    fireEvent.click(screen.getByText("Collapse all"));
    expect(screen.queryByText("NIFTY 23600 CE")).toBeNull();
  });
});
