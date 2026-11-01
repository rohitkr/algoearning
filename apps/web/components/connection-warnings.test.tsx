import type { Connections } from "@algoearning/api-types";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ConnectionNotices } from "./connection-warnings";

afterEach(cleanup);

const zerodha = {
  id: "a1",
  broker_name: "Zerodha",
  client_id: "UQV780",
  label: "Main",
  status: "expired",
} as const;

describe("connection warnings", () => {
  it("says nothing while everything is connected or outside trading hours", () => {
    const { container, rerender } = render(
      <ConnectionNotices data={{ market_data: { state: "ok" }, brokers: [] }} isAdmin />,
    );
    expect(container.textContent).toBe("");
    rerender(<ConnectionNotices data={{ market_data: { state: "idle" }, brokers: [] }} isAdmin />);
    expect(container.textContent).toBe("");
  });

  it("warns that market data and the broker login are pending, with where to log in", () => {
    const data: Connections = { market_data: { state: "login_needed" }, brokers: [zerodha] };
    render(<ConnectionNotices data={data} isAdmin />);
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("Market data is not connected yet");
    expect(text).toContain("Zerodha UQV780 (Main) is not connected: today's login is pending");
    expect(screen.getByRole("link", { name: "Log in under Market data" }).getAttribute("href")).toBe(
      "/monitor/market-data",
    );
    expect(screen.getByRole("link", { name: "Log in" }).getAttribute("href")).toBe("/brokers");
  });

  it("tells a user who is not an admin about the feed without a Monitor link", () => {
    render(<ConnectionNotices data={{ market_data: { state: "no_prices" }, brokers: [] }} isAdmin={false} />);
    expect(screen.getByRole("alert").textContent).toContain("no prices have arrived for 2 minutes");
    expect(screen.queryByRole("link")).toBeNull();
  });
});
