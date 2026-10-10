import type { StrategyConfig } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StrategyTest } from "./strategy-test";

const auth = { getToken: async () => "token" }; // stable between renders, like Clerk's
vi.mock("@clerk/nextjs", () => ({ useAuth: () => auth }));
vi.mock("@/components/reports/pnl-charts", () => ({ DailyPnlBars: () => null, EquityCurve: () => null }));

const CONFIG = { kind: "rules", underlying: "NIFTY", legs: [] } as unknown as StrategyConfig;
const RESULT = {
  summary: { net_pnl: 1234, gross_pnl: 1300, charges: 66, trades: 3, wins: 2, losses: 1, win_rate: 0.67, trading_days: 3, days_replayed: 20 },
  daily: [], trades: [], trades_total: 0, warnings: ["Quantities use today's lot size (75 for NIFTY)."],
}; // prettier-ignore

let posted: { path: string; body: Record<string, unknown> }[] = [];
beforeEach(() => {
  posted = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/coverage")) {
        return new Response(
          JSON.stringify([
            {
              underlying: "NIFTY",
              index_from: "2026-01-01",
              index_to: "2026-10-09",
              option_from: "2026-06-02",
              option_to: "2026-10-09",
            },
          ]),
          { status: 200 },
        );
      }
      posted.push({ path, body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({ underlying: "NIFTY", result: RESULT }), { status: 200 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StrategyTest", () => {
  it("tests the unsaved config over the last month of stored option history and flags a stale result", async () => {
    const { rerender } = render(<StrategyTest config={CONFIG} blocked={false} />);
    const button = await screen.findByText("Test now");
    await screen.findByDisplayValue("2026-10-09");
    await act(async () => fireEvent.click(button));
    expect(posted[0]).toEqual({
      path: "/v1/backtests/preview",
      body: {
        config: CONFIG,
        start_date: "2026-09-09",
        end_date: "2026-10-09",
        multiplier: 1,
        slippage_pct: 0.05,
      },
    });
    expect(await screen.findByText("Test again")).toBeTruthy();
    expect(screen.getByText("Quantities use today's lot size (75 for NIFTY).")).toBeTruthy();
    expect(screen.queryByText("Parameters changed since this result")).toBeNull();
    rerender(<StrategyTest config={{ ...CONFIG, underlying: "SENSEX" } as StrategyConfig} blocked={false} />);
    expect(screen.getByText("Parameters changed since this result")).toBeTruthy();
  });

  it("will not run while the strategy has errors", async () => {
    render(<StrategyTest config={CONFIG} blocked />);
    const button = (await screen.findByText("Test now")).closest("button");
    expect(button?.disabled).toBe(true);
  });
});
