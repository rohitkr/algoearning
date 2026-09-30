import type { Instrument, StrategyLeg, TimeBasedConfig } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import {
  configSummary,
  describeConfig,
  describeLeg,
  issueKey,
  issueLabel,
  issueMap,
  newLeg,
  nextLegId,
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
});
