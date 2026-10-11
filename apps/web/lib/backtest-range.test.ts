import { describe, expect, it } from "vitest";

import { defaultRange, monthsBefore } from "./backtest-range";

describe("backtest range", () => {
  it("goes back three calendar months, clamping to the month end", () => {
    expect(monthsBefore("2026-10-09", 3)).toBe("2026-07-09");
    expect(monthsBefore("2026-05-31", 3)).toBe("2026-02-28");
    expect(monthsBefore("2026-02-10", 3)).toBe("2025-11-10");
  });

  it("defaults to the last 3 months of option data and never before it starts", () => {
    const c = {
      underlying: "NIFTY",
      index_from: "2026-01-01",
      index_to: "2026-10-09",
      option_from: "2026-06-02",
      option_to: "2026-10-09",
    };
    expect(defaultRange(c as never)).toEqual({ from: "2026-07-09", to: "2026-10-09" });
    expect(defaultRange({ ...c, option_from: "2026-09-01" } as never)).toEqual({
      from: "2026-09-01",
      to: "2026-10-09",
    });
    expect(defaultRange(null)).toEqual({ from: "", to: "" });
  });
});
