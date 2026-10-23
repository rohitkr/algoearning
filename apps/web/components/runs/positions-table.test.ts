import type { RunDetail } from "@algoearning/api-types";
import { describe, expect, it } from "vitest";

import { currentPositions, totalPnl } from "./positions-table";

type P = RunDetail["positions"][number];
const pos = (id: string, status: string, entry: string, exit: string | null): P =>
  ({ id, status, entry_time: entry, exit_time: exit }) as P;

describe("which positions the run page shows", () => {
  const now = new Date("2026-10-15T04:00:00Z"); // 09:30 IST on the 15th
  const list = [
    pos("held", "open", "2026-10-13T09:40:00Z", null), // BTST: opened two days ago, still open
    pos("today-open", "open", "2026-10-15T03:50:00Z", null),
    pos("closed-today", "closed", "2026-10-13T09:40:00Z", "2026-10-15T03:55:00Z"), // yesterday's, closed this morning
    pos("old", "closed", "2026-10-13T09:40:00Z", "2026-10-14T07:00:00Z"),
  ];
  it("shows what is open and what happened today by default, hiding earlier closed trades", () => {
    expect(currentPositions(list, "current", now).map((p) => p.id)).toEqual([
      "held",
      "today-open",
      "closed-today",
    ]);
  });
  it("can show only open ones, or everything", () => {
    expect(currentPositions(list, "open", now).map((p) => p.id)).toEqual(["held", "today-open"]);
    expect(currentPositions(list, "all", now)).toHaveLength(4);
  });
  it("counts a trade closed just after midnight IST as today's", () => {
    const late = pos("late", "closed", "2026-10-14T09:40:00Z", "2026-10-14T19:00:00Z"); // 00:30 IST on the 15th
    expect(currentPositions([late], "current", now)).toHaveLength(1);
  });
});

describe("the total under the positions", () => {
  it("adds the P&L of the positions shown, booked and open", () => {
    const ps = [{ pnl: -21762 }, { pnl: 7059 }, { pnl: 10413 }, { pnl: -3159 }, { pnl: null }] as P[];
    expect(totalPnl(ps)).toBe(-7449);
  });
});
