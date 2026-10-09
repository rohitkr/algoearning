import type { SignalSource, TipReplay } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TipReplayView } from "./tip-replay";

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const SOURCE = { id: "s1", chat_id: -1, chat_title: "VIP" } as SignalSource;
const trade = (id: number, entry: "IN_ZONE" | "CHASED" | "NOT_PLACED", net: number, note: string) => ({
  signal_id: id, date: "2026-10-08T04:45:00Z", tip: "BUY NIFTY 22450 CE", direction: "BULLISH" as const,
  entry_low: 150, entry_high: 154, stop_loss: 135, targets: [169, 184, 204], channel_status: "T1" as const,
  entry, note, expiry: "2026-10-13", price_at_signal: 158, above_zone: 4, buffer: 5,
  entry_time: entry === "NOT_PLACED" ? null : "2026-10-08T04:46:00Z", entry_price: entry === "NOT_PLACED" ? null : 158,
  qty: entry === "NOT_PLACED" ? 0 : 225,
  exits: entry === "NOT_PLACED" ? [] : [{ time: "2026-10-08T05:00:00Z", price: 169, qty: 75, reason: "TARGET 1" }],
  gross: net, charges: 0, net,
}); // prettier-ignore
const REPLAY: TipReplay = {
  start: "2026-07-09", end: "2026-10-09", lots: 3, lot_sizes: { NIFTY: 75 }, buffers: { NIFTY: 5, SENSEX: 10 },
  slippage_pct: 0.05,
  summary: { tips: 2, traded: 1, entries: { CHASED: 1, NOT_PLACED: 1 }, net_pnl: 825, gross_pnl: 825, charges: 0, win_rate: 1,
    avg_win: 825, avg_loss: null, profit_factor: null, max_drawdown: 0 },
  daily: [{ day: "2026-10-08", pnl: 825, trades: 1, cumulative: 825 }],
  trades: [trade(2, "NOT_PLACED", 0, "price 170 was already above the range"), trade(1, "CHASED", 825, "bought at 158")],
  warnings: ["Expiries are inferred."],
}; // prettier-ignore

let url = "";
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (u: string) => {
      url = u;
      return new Response(JSON.stringify(REPLAY), { status: 200 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TipReplayView", () => {
  it("runs from the first tip with the buffers and marks chased and not-placed entries", async () => {
    render(<TipReplayView source={SOURCE} />);
    await act(async () => fireEvent.click(screen.getByText("Run replay")));
    expect(url).toContain(
      "/v1/signal-sources/s1/replay?start=2026-07-09&lots=3&nifty_buffer=5&sensex_buffer=10",
    );
    expect(screen.getByText("Chased +4")).toBeTruthy();
    expect(screen.getByText("Not placed")).toBeTruthy();
    expect(screen.getByText("price 170 was already above the range")).toBeTruthy();
    expect(screen.getByText("TARGET 1: sold 75 @ 169 at 10:30")).toBeTruthy();
    expect(screen.getByText("Expiries are inferred.")).toBeTruthy();
  });
});
