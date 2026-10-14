import type { SyncDay } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import { casMark, dayLabel, eta, isActive, monthsOf } from "./data-sync-admin";

const day = (over: Partial<SyncDay>): SyncDay => ({
  day: "2026-08-04",
  status: "ok",
  index_bars: 375,
  options_expected: 0,
  options_complete: 0,
  options_partial: 0,
  options_missing: 0,
  closing_missing: null,
  closing_have: 0,
  closing_empty: 0,
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
    expect(dayLabel(day({ status: "closed", day: "2026-08-04" }))).toContain("holiday");
    expect(dayLabel(day({ status: "closed", day: "2026-08-02" }))).toContain("weekend");
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
    expect(t).toContain("2 not fetched yet");
  });

  it("estimates the time left from the pace so far", () => {
    const now = Date.parse("2026-08-04T10:10:00Z");
    expect(eta(10, 20, "2026-08-04T10:00:00Z", now)).toBe("10 min");
    expect(eta(1, 100, "2026-08-04T10:00:00Z", now)).toBe("16 h 30 min");
    expect(eta(0, 20, "2026-08-04T10:00:00Z", now)).toBeNull();
    expect(eta(20, 20, "2026-08-04T10:00:00Z", now)).toBeNull();
    expect(eta(5, 20, null, now)).toBeNull();
  });

  it("marks the closing session on each day since it began", () => {
    expect(casMark(day({ closing_missing: null }))).toBeNull(); // before 3 Aug 2026
    expect(casMark(day({ closing_missing: 3, closing_have: 5 }))).toBe("bg-black");
    expect(casMark(day({ closing_missing: 0, closing_have: 5, closing_empty: 2 }))).toBe("bg-white");
    expect(casMark(day({ closing_missing: 0, closing_have: 0, closing_empty: 7 }))).toBe("bg-sky-400");
    expect(dayLabel(day({ closing_missing: 0, closing_have: 5, closing_empty: 2 }))).toContain(
      "5 contracts with candles",
    );
  });

  it("knows which jobs are still going", () => {
    expect(isActive({ status: "pending" })).toBe(true);
    expect(isActive({ status: "running" })).toBe(true);
    expect(isActive({ status: "done" })).toBe(false);
  });
});
