import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PricesAsOf, StalePricesNotice, istClock } from "./run-bits";

afterEach(cleanup);

describe("run price notices", () => {
  it("shows a time in Indian time whatever the browser's zone", () => {
    expect(istClock("2026-10-13T10:09:30Z")).toBe("15:39:30");
    expect(istClock(null)).toBeNull();
  });

  it("says what the open P&L is as of, only while positions are open", () => {
    const { rerender } = render(
      <PricesAsOf run={{ prices_at: "2026-10-13T10:09:30Z", open_positions: 4 }} />,
    );
    expect(screen.getByText("prices as of 15:39:30 IST")).toBeTruthy();
    rerender(<PricesAsOf run={{ prices_at: "2026-10-13T10:09:30Z", open_positions: 0 }} />);
    expect(screen.queryByText(/prices as of/)).toBeNull();
  });

  it("warns plainly when live prices stopped during market hours, and says nothing otherwise", () => {
    const { rerender } = render(
      <StalePricesNotice run={{ prices_stale: true, prices_at: "2026-10-13T09:50:00Z" }} />,
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "Live prices have stopped (last price 15:20:00 IST)",
    );
    expect(screen.getByRole("alert").textContent).toContain("stop-losses and limits are not being watched");
    rerender(<StalePricesNotice run={{ prices_stale: false, prices_at: null }} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
