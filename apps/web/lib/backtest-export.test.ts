import { describe, expect, it } from "vitest";

import type { BacktestResultData, Trade } from "@/components/backtesting/result-view";

import { fileName, summaryRows, tradesCsv } from "./backtest-export";

const trade: Trade = {
  leg: "A",
  contract: 'NIFTY "15 Sep" 23600 CE',
  side: "BUY",
  qty: 650,
  entry_time: "2026-09-15T09:20:00+05:30",
  entry_price: 17.3,
  exit_time: "2026-09-16T09:32:00+05:30",
  exit_price: 11.85,
  reason: "strategy profit reached, 6000",
  gross: -3542.5,
  charges: 64.8,
  net: -3607.3,
};

describe("backtest export", () => {
  it("writes one escaped CSV row per trade with the weekday and both fills", () => {
    const lines = tradesCsv([trade]).replace("﻿", "").trim().split("\r\n");
    expect(lines[0]).toBe(
      "Day,Entry date,Symbol,Side,Qty,Entry time,Entry price,Exit time,Exit price,Gross P&L,Charges,Net P&L,Exit reason",
    );
    expect(lines[1]).toBe(
      'Tue,15 Sept 2026,"NIFTY ""15 Sep"" 23600 CE",BUY,650,15 Sept 2026 09:20,17.3,16 Sept 2026 09:32,11.85,-3542.5,64.8,-3607.3,"strategy profit reached, 6000"',
    );
  });

  it("names files after the strategy and range", () => {
    expect(fileName({ title: "Long Iron Condor!", from: "2026-09-12", to: "2026-10-12" }, "pdf")).toBe(
      "long-iron-condor_2026-09-12_2026-10-12.pdf",
    );
  });

  it("lists the figures a report needs", () => {
    const res = {
      summary: { net_pnl: 1234.5, trades: 4, wins: 3, losses: 1, win_rate: 0.75 },
    } as unknown as BacktestResultData;
    const rows = Object.fromEntries(summaryRows(res));
    expect(rows["Net P&L"]).toBe("1,234.50");
    expect(rows["Win rate"]).toBe("75.0%");
    expect(rows["Trades"]).toBe("4 (3 won, 1 lost)");
  });
});
