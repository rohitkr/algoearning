import type { StrategyConfig } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import { tradesSource } from "./tip-strategy-backtest";

describe("tradesSource", () => {
  it("finds tip strategies of this source, in rules and in plans", () => {
    const rules = { kind: "rules", entry: { mode: "tip", source_id: "s1" } } as unknown as StrategyConfig;
    const plans = {
      kind: "plans",
      plans: [{ entry: { mode: "time" } }, { entry: { mode: "tip", source_id: "s1" } }],
    };
    expect(tradesSource(rules, "s1")).toBe(true);
    expect(tradesSource(rules, "s2")).toBe(false);
    expect(tradesSource(plans as unknown as StrategyConfig, "s1")).toBe(true);
    expect(tradesSource({ kind: "rules", entry: { mode: "time" } } as unknown as StrategyConfig, "s1")).toBe(
      false,
    );
  });
});
