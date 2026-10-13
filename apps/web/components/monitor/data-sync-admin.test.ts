import type { SyncDay } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import { dayLabel, eta, isActive, monthsOf } from "./data-sync-admin";

const day = (over: Partial<SyncDay>): SyncDay => ({
  day: "2026-08-04",
  status: "ok",
  index_bars: 375,
  options_expected: 0,
  options_complete: 0,
  options_partial: 0,
  options_missing: 0,
  closing_missing: null,
  ...over,
});

describe("data sync helpers", () => {
  it("lays months out from the first to the last day", () => {
    const m = monthsOf([day({ day: "2026-08-04" })], "2026-07-30", "2026-08-05");
    expect(m.map((x) => x.key)).toEqual(["2026-07", "2026-08"]);
    expect(m[0]!.cells).toHaveLength(31);
    expect(m[1]!.cells.find((c) => c.date === "2026-08-04")?.d?.status).toBe("ok");
    expect(m[1]!.lead).toBe(6); // 1 Aug 2026 is a Saturday
  });

  it("describes a day plainly", () => {
    expect(dayLabel(day({ status: "closed" }))).toContain("Aug");
    const t = dayLabel(
      day({
        status: "partial",
        options_expected: 5,
        options_complete: 3,
        options_partial: 1,
        options_missing: 1,
        closing_missing: 2,
      }),
    );
    expect(t).toContain("3 complete, 1 partial, 1 missing of 5");
    expect(t).toContain("2 without 15:30");
  });

  it("estimates the time left from the pace so far", () => {
    const now = Date.parse("2026-08-04T10:10:00Z");
    expect(eta(10, 20, "2026-08-04T10:00:00Z", now)).toBe("10 min");
    expect(eta(1, 100, "2026-08-04T10:00:00Z", now)).toBe("16 h 30 min");
    expect(eta(0, 20, "2026-08-04T10:00:00Z", now)).toBeNull();
    expect(eta(20, 20, "2026-08-04T10:00:00Z", now)).toBeNull();
    expect(eta(5, 20, null, now)).toBeNull();
  });

  it("knows which jobs are still going", () => {
    expect(isActive({ status: "pending" })).toBe(true);
    expect(isActive({ status: "running" })).toBe(true);
    expect(isActive({ status: "done" })).toBe(false);
  });
});
