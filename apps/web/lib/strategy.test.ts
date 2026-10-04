import type { Instrument, RulesConfig, StrategyLeg, TimeBasedConfig } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import {
  configSummary,
  describeConfig,
  describeLeg,
  groupText,
  issueKey,
  issueLabel,
  issueMap,
  newLeg,
  nextLegId,
  rulesFromTimeBased,
  strikeLabel,
} from "./strategy";

const NIFTY: Instrument = {
  code: "NIFTY",
  name: "Nifty 50",
  exchange: "NFO",
  lot_size: 65,
  strike_step: 50,
  weekly_expiry: true,
  session_open: "09:15",
  session_close: "15:40",
  refreshed_at: null,
};

const leg = (over: Partial<StrategyLeg> = {}): StrategyLeg => ({
  id: "L1",
  action: "SELL",
  option_type: "CE",
  lots: 2,
  expiry: "current_week",
  strike: { mode: "atm", offset: 0, premium: null },
  ...over,
});

describe("strategy text", () => {
  it("labels strikes", () => {
    expect(strikeLabel({ mode: "atm", offset: 0 })).toBe("ATM");
    expect(strikeLabel({ mode: "atm", offset: 2 })).toBe("OTM 2");
    expect(strikeLabel({ mode: "atm", offset: -1 })).toBe("ITM 1");
    expect(strikeLabel({ mode: "premium", offset: 0, premium: 50 })).toBe("Premium ≈ ₹50");
    expect(strikeLabel({ mode: "premium_gte", offset: 0, premium: 60 })).toBe("Premium ≥ ₹60");
    expect(strikeLabel({ mode: "premium_lte", offset: 0, premium: 60 })).toBe("Premium ≤ ₹60");
    expect(strikeLabel({ mode: "points", offset: 0, points: 300 })).toBe("300 pts OTM");
    expect(strikeLabel({ mode: "points", offset: 0, points: -100 })).toBe("100 pts ITM");
  });

  it("describes rules held overnight and converts time-based configs", () => {
    const c: RulesConfig = {
      kind: "rules",
      underlying: "NIFTY",
      entry: {
        mode: "time",
        at: "15:00",
        days: ["MON", "TUE", "WED", "THU", "FRI"],
        dte: [0, 1],
        max_entries: 1,
      },
      holding: { mode: "next_day", exit: "09:30", days: 1 },
      legs: [leg()],
      risk: {
        exit_all_on_leg_sl: false,
        mtm_stop_loss: 3000,
        lock_profit: { at: 2000, lock: 1000, trail_every: 500, trail_by: 250 },
      },
    };
    const lines = describeConfig(c, [NIFTY]);
    expect(lines[0]).toBe(
      "Enter at 15:00, every weekday, only on expiry day or 1 trading day before expiry; hold overnight and exit at 09:30 the next trading day.",
    );
    expect(lines).toContain("Exit everything if the trade's loss reaches ₹3000.");
    expect(lines).toContain(
      "Once the profit reaches ₹2000, keep at least ₹1000, raised by ₹250 for every ₹500 more.",
    );
    expect(configSummary(c)).toBe("NIFTY · 1 leg · 15:00–next day 09:30");

    const old: TimeBasedConfig = {
      kind: "time_based",
      underlying: "NIFTY",
      timing: { entry: "09:30", exit: "15:00", days: ["MON"] },
      legs: [leg()],
    };
    const r = rulesFromTimeBased(old);
    expect(r.entry).toMatchObject({ at: "09:30", days: ["MON"] });
    expect(r.holding).toEqual({ mode: "intraday", exit: "15:00", days: 1 });
    expect(configSummary(r)).toBe("NIFTY · 1 leg · 09:30–15:00");
  });

  it("describes a leg with quantity and exits", () => {
    const text = describeLeg(
      leg({
        stop_loss: { unit: "percent", value: 30, basis: "premium" },
        trailing: { unit: "points", trigger: 10, step: 5 },
        reentry_on_sl: { mode: "at_cost", count: 1 },
      }),
      "NIFTY",
      NIFTY,
    );
    expect(text).toBe(
      "Sell 2 lots (130 qty) NIFTY ATM CE · Current week · SL 30% · Trail 5 pts every 10 pts · Re-enter after SL ×1",
    );
  });

  it("describes whole strategies", () => {
    const c: TimeBasedConfig = {
      kind: "time_based",
      underlying: "NIFTY",
      timing: { entry: "09:20", exit: "15:15", days: ["MON", "THU"] },
      legs: [leg()],
      risk: { mtm_stop_loss: 3000, mtm_target: null, exit_all_on_leg_sl: false },
    };
    const lines = describeConfig(c, [NIFTY]);
    expect(lines[0]).toBe("Enter at 09:20 and exit at 15:15, Mon, Thu.");
    expect(lines.at(-1)).toBe("Exit everything if the loss reaches ₹3000.");
    expect(configSummary(c)).toBe("NIFTY · 1 leg · 09:20–15:15");
    const zero = describeConfig(
      {
        kind: "zero_dte",
        underlying: "NIFTY",
        first_entry: "09:20",
        last_entry: "14:30",
        step_minutes: 10,
        exit_time: "15:15",
        stop_loss_pct: 30,
        reentry: true,
        lookback: 8,
        lots: 1,
        itm_points: 100,
        hedge_width: null,
      },
      [NIFTY],
    );
    expect(zero[0]).toContain(
      "sell 1 lot (65 qty) each of a call and a put 100 points in the money, unhedged",
    );
  });

  it("numbers new legs after the existing ones", () => {
    expect(nextLegId([leg({ id: "L1" }), leg({ id: "L3" })])).toBe("L4");
    expect(nextLegId([leg({ id: "L2" })])).toBe("L3");
    const c = { kind: "time_based", underlying: "BANKNIFTY", legs: [leg()] } as TimeBasedConfig;
    expect(newLeg(c, false)).toMatchObject({ id: "L2", expiry: "current_month" });
  });

  it("maps issues from validate and from save to the same keys", () => {
    expect(issueKey(["legs", 0, "lots"])).toBe("legs.0.lots");
    expect(issueKey(["body", "config", "time_based", "legs", 0, "lots"])).toBe("legs.0.lots");
    expect(issueKey(["body", "config", "timing", "exit"])).toBe("timing.exit");
    expect(
      issueMap([
        { loc: ["timing", "exit"], msg: "first", type: "x" },
        { loc: ["timing", "exit"], msg: "second", type: "x" },
      ]),
    ).toEqual({ "timing.exit": "first" });
    expect(issueLabel("legs.1.stop_loss.value")).toBe("Leg 2 · stop loss · value");
    expect(issueLabel("")).toBe("Strategy");
  });

  it("words conditions plainly", () => {
    expect(
      groupText({
        match: "any",
        conditions: [
          {
            timeframe: 15,
            left: { kind: "price", field: "close" },
            op: "crosses_above",
            right: { kind: "level", name: "prev_high", minutes: 15, at: null, offset: 0 },
          },
          {
            timeframe: 1,
            left: { kind: "price", field: "low" },
            op: "below",
            right: { kind: "level", name: "price_at", minutes: 15, at: "09:20", offset: -50 },
          },
        ],
      }),
    ).toBe(
      "the 15-minute close crosses above previous day's high or the 1-minute low is below the price at 09:20 − 50",
    );
  });
});
