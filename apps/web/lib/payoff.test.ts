import { describe, expect, it } from "vitest";

import { analyse, atExpiry, bs, impliedVol, type PayoffLeg } from "./payoff";

const ironFly: PayoffLeg[] = [
  { action: "BUY", right: "PE", strike: 22400, qty: 65, price: 64.15, expiry: "2026-10-27" },
  { action: "SELL", right: "PE", strike: 22600, qty: 65, price: 137.65, expiry: "2026-10-27" },
  { action: "SELL", right: "CE", strike: 22600, qty: 65, price: 132.45, expiry: "2026-10-27" },
  { action: "BUY", right: "CE", strike: 22800, qty: 65, price: 58.45, expiry: "2026-10-27" },
];

describe("payoff", () => {
  it("an iron fly: limited profit and loss, two breakevens around the short strike", () => {
    const a = analyse(ironFly, 22600, new Date("2026-10-25T05:00:00Z"));
    const credit = 137.65 + 132.45 - 64.15 - 58.45; // 147.5 per unit
    expect(a.premium).toBeCloseTo(credit * 65, 1);
    expect(a.maxProfit).toBeCloseTo(credit * 65, 0); // ₹9,588 at 22600
    expect(a.maxLoss).toBeCloseTo((credit - 200) * 65, 0); // -₹3,413 beyond the wings
    expect(a.breakevens).toEqual([22453, 22748]);
    expect(atExpiry(ironFly, 22600)).toBeCloseTo(credit * 65, 1);
  });

  it("a short call is unlimited on the loss side", () => {
    const a = analyse(
      [{ action: "SELL", right: "CE", strike: 22600, qty: 65, price: 130, expiry: "2026-10-27" }],
      22600,
    );
    expect(a.maxLoss).toBeNull();
    expect(a.maxProfit).toBeCloseTo(130 * 65, 0);
  });

  it("solves the volatility back from a price", () => {
    const p = bs("CE", 22600, 22700, 5 / 365, 0.14);
    expect(impliedVol("CE", 22600, 22700, 5 / 365, p)).toBeCloseTo(0.14, 3);
  });
});
