import type { StrategyCatalog } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import fixture from "./catalog.fixture.json";
import { StrategyBuilder } from "./strategy-builder";

// The catalog as GET /v1/strategies/catalog returns it (regenerate from a running API if presets change).
const catalog = fixture as StrategyCatalog;

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

type Call = { method: string; path: string; body: unknown };
let calls: Call[] = [];
let validateReply: unknown = { valid: true, errors: [], warnings: [] };

beforeEach(() => {
  vi.useFakeTimers();
  calls = [];
  push.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      calls.push({
        method: init.method ?? "GET",
        path,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      const reply = path.endsWith("/validate")
        ? validateReply
        : { id: "11111111-1111-1111-1111-111111111111" };
      return new Response(JSON.stringify(reply), { status: path.endsWith("/validate") ? 200 : 201 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const legs = () => screen.queryAllByRole("group").filter((g) => g.tagName === "FIELDSET");
const flush = () => act(async () => void (await vi.advanceTimersByTimeAsync(500)));

describe("StrategyBuilder", () => {
  it("starts from a preset, edits legs and saves the config", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    expect(screen.getByRole("heading", { name: "New strategy" })).toBeTruthy();
    expect(legs()).toHaveLength(1); // "Start from scratch"

    fireEvent.click(screen.getByRole("radio", { name: /Short straddle/ }));
    expect(legs()).toHaveLength(2);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Short straddle");
    expect(
      screen.getByText(/Leg 1: Sell 1 lot \(65 qty\) NIFTY ATM CE · Current week · SL 30%/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Add leg/ }));
    expect(legs()).toHaveLength(3);
    fireEvent.click(within(legs()[2]!).getByRole("radio", { name: "BUY" }));
    fireEvent.change(within(legs()[2]!).getByLabelText("Strike"), { target: { value: "4" } });
    expect(screen.getByText(/Leg 3: Buy 1 lot \(65 qty\) NIFTY OTM 4 CE/)).toBeTruthy();

    await flush();
    const validate = calls.filter((c) => c.path === "/v1/strategies/validate");
    expect(validate.length).toBeGreaterThan(0); // debounced: one call for the burst of edits
    expect(screen.getByText("Ready to save")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const post = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies");
    const cfg = (post?.body as { config: { legs: { id: string; action: string; strike: unknown }[] } })
      .config;
    expect(cfg.legs.map((l) => `${l.id}:${l.action}`)).toEqual(["L1:SELL", "L2:SELL", "L3:BUY"]);
    expect(cfg.legs[2]!.strike).toEqual({ mode: "atm", offset: 4, premium: null });
    expect(push).toHaveBeenCalledWith("/builder/11111111-1111-1111-1111-111111111111?saved=1");
  });

  it("moves legs to monthly expiries for monthly-only indices", () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.change(screen.getByLabelText("Underlying"), { target: { value: "BANKNIFTY" } });
    expect((within(legs()[0]!).getByLabelText("Expiry") as HTMLSelectElement).value).toBe("current_month");
    expect(screen.getByText(/Lot size 30 · strikes every 100 · monthly expiries/)).toBeTruthy();
  });

  it("shows the server's errors next to the field and plan warnings", async () => {
    validateReply = {
      valid: false,
      errors: [{ loc: ["timing", "exit"], msg: "must be after the entry time", type: "value_error" }],
      warnings: [
        { loc: ["legs", 0, "lots"], msg: "your plan allows 10 lot(s) per order", type: "plan_limit" },
      ],
    };
    render(<StrategyBuilder catalog={catalog} />);
    await flush();
    expect(screen.getAllByText("must be after the entry time")).toHaveLength(2); // inline + checks panel
    expect(screen.getByText("1 to fix")).toBeTruthy();
    expect(screen.getAllByText(/your plan allows 10 lot\(s\) per order/).length).toBeGreaterThan(0);
    validateReply = { valid: true, errors: [], warnings: [] };
  });

  it("shows the proven strategies' parameters instead of legs", () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /2h range breakout/ }));
    expect(legs()).toHaveLength(0);
    expect((screen.getByLabelText("Range until") as HTMLInputElement).value).toBe("11:15");
    fireEvent.change(screen.getByLabelText("Hedge wing"), { target: { value: "" } });
    expect(screen.getByText(/, unhedged\./)).toBeTruthy();
  });
});
