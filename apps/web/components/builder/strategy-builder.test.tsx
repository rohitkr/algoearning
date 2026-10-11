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
    const cfg = (
      post?.body as {
        config: { kind: string; plans: { legs: { id: string; action: string; strike: unknown }[] }[] };
      }
    ).config;
    expect(cfg.kind).toBe("plans");
    const legsOut = cfg.plans[0]!.legs;
    expect(legsOut.map((l) => `${l.id}:${l.action}`)).toEqual(["L1:SELL", "L2:SELL", "L3:BUY"]);
    expect(legsOut[2]!.strike).toEqual({ mode: "atm", offset: 4, premium: null, points: null });
    expect(push).toHaveBeenCalledWith("/builder/11111111-1111-1111-1111-111111111111?saved=1");
  });

  it("moves legs to monthly expiries for monthly-only indices", () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "BANKNIFTY" } });
    expect((within(legs()[0]!).getByLabelText("Expiry") as HTMLSelectElement).value).toBe("current_month");
    expect(screen.getByText(/Lot size 30 · strikes every 100 · monthly expiries/)).toBeTruthy();
  });

  it("shows the server's errors next to the field and plan warnings", async () => {
    validateReply = {
      valid: false,
      errors: [
        { loc: ["plans", 0, "holding", "exit"], msg: "must be after the entry time", type: "value_error" },
      ],
      warnings: [
        {
          loc: ["plans", 0, "legs", 0, "lots"],
          msg: "your plan allows 10 lot(s) per order",
          type: "plan_limit",
        },
      ],
    };
    render(<StrategyBuilder catalog={catalog} />);
    await flush();
    expect(screen.getAllByText("must be after the entry time")).toHaveLength(2); // inline + checks panel
    expect(screen.getByText("1 to fix")).toBeTruthy();
    expect(screen.getAllByText(/your plan allows 10 lot\(s\) per order/).length).toBeGreaterThan(0);
    validateReply = { valid: true, errors: [], warnings: [] };
  });

  it("builds an overnight straddle by premium and saves its schedule", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /Overnight straddle/ }));
    expect(legs()).toHaveLength(2);
    expect(
      screen.getByText(
        /Enter at 15:00 \(not after 15:20\), every weekday; hold overnight and exit at 09:30 the next trading day\./,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Leg 1: Sell 1 lot \(65 qty\) NIFTY Premium ≈ ₹60 CE/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Hold"), { target: { value: "days" } });
    fireEvent.change(screen.getByLabelText("Trading days"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "2d" }));
    expect(
      screen.getByText(/only 2 trading days before expiry; hold and exit at 09:30, 2 trading days later/),
    ).toBeTruthy();
    fireEvent.change(within(legs()[1]!).getByLabelText("Strike"), { target: { value: "points" } });
    fireEvent.change(within(legs()[1]!).getByLabelText("Points"), { target: { value: "300" } });
    expect(screen.getByText(/Leg 2: Sell 1 lot \(65 qty\) NIFTY 300 pts OTM PE/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Combined premium stop (sold legs together)"));
    fireEvent.click(screen.getByLabelText("Lock profit (and trail it)"));
    expect(screen.getByText(/sold premiums together rise 30% above/)).toBeTruthy();
    expect(screen.getByText(/Once the profit reaches ₹2000, keep at least ₹1000\./)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: {
        kind: string;
        plans: { entry: { at: string; dte: number[] }; holding: object; legs: { strike: object }[] }[];
      };
    };
    expect(saved.config.kind).toBe("plans");
    const plan = saved.config.plans[0]!;
    expect(plan.entry).toMatchObject({ at: "15:00", dte: [2] });
    expect(plan.holding).toEqual({ mode: "days", exit: "09:30", days: 2 });
    expect(plan.legs[1]!.strike).toEqual({ mode: "points", offset: 0, premium: null, points: 300 });
  });

  it("builds a breakout from the opening range preset and edits its signals", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /Opening range breakout/ }));
    expect(legs()).toHaveLength(2);
    expect(
      screen.getByText(/Up signal: when a 5-minute close crosses above the 15-minute opening range high\./),
    ).toBeTruthy();
    expect(
      screen.getByText(/Down signal: when a 5-minute close crosses below the 15-minute opening range low\./),
    ).toBeTruthy();
    expect(screen.getByText(/Exit everything on a signal in the other direction\./)).toBeTruthy();
    expect(
      screen.getByText(/Leg 1: Buy 1 lot \(65 qty\) NIFTY ATM CE · Current week · SL 30% · on an up signal/),
    ).toBeTruthy();

    fireEvent.change(screen.getAllByLabelText("Candle")[0]!, { target: { value: "15" } });
    fireEvent.change(screen.getAllByLabelText("Compared with")[0]!, { target: { value: "prev_high" } });
    expect(
      screen.getByText(/Up signal: when a 15-minute close crosses above previous day high\./),
    ).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: /Add condition/ })[0]!);
    expect(
      screen.getByText(/ and a 5-minute close crosses above the 15-minute opening range high\./),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: {
        plans: {
          entry: { mode: string; signals: { direction: string; conditions: { candle: number }[] }[] };
        }[];
      };
    };
    const entry = saved.config.plans[0]!.entry;
    expect(entry.mode).toBe("conditions");
    expect(entry.signals[0]).toMatchObject({ direction: "up" });
    expect(entry.signals[0]!.conditions.map((c) => c.candle)).toEqual([15, 5]);

    fireEvent.change(screen.getByLabelText("Enter"), { target: { value: "time" } });
    expect(screen.queryByText("Start from:")).toBeNull();
  });

  it("builds an indicator strategy and saves its settings", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /EMA 9\/21 crossover/ }));
    expect(
      screen.getByText(/EMA\(9\) crosses above EMA\(21\) \(5-minute candles\) and RSI\(14\) is above 50/),
    ).toBeTruthy();

    // the first condition's right side: EMA 21 -> Supertrend (10, 3)
    fireEvent.change(screen.getAllByLabelText("Indicator")[1]!, { target: { value: "supertrend" } });
    expect(screen.getAllByLabelText("ATR multiplier")).toHaveLength(1);
    fireEvent.change(screen.getAllByLabelText("Period")[1]!, { target: { value: "7" } });
    expect(screen.getByText(/EMA\(9\) crosses above Supertrend \(7, 3\)/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: { plans: { entry: { signals: { conditions: { right: object }[] }[] } }[] };
    };
    expect(saved.config.plans[0]!.entry.signals[0]!.conditions[0]!.right).toMatchObject({
      kind: "indicator",
      indicator: "supertrend",
      period: 7,
      multiplier: 3,
    });
  });

  it("trades the direction of a Telegram tip by selling the opposite side", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.change(screen.getByLabelText("Enter"), { target: { value: "tip" } });
    expect(screen.getByRole("heading", { name: "Telegram tip" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Sell the opposite side (ATM)" }));
    expect(legs()).toHaveLength(2);
    expect(screen.getByText(/a bullish tip trades the up legs, a bearish one the down legs/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "VIP tips, sell opposite" } });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: {
        plans: { entry: { mode: string; max_tip_age_s: number; on_tip_exit: string }; legs: object[] }[];
      };
    };
    const tipPlan = saved.config.plans[0]!;
    expect(tipPlan.entry).toMatchObject({ mode: "tip", max_tip_age_s: 120, on_tip_exit: "close" });
    expect(tipPlan.legs).toMatchObject([
      { action: "SELL", option_type: "PE", direction: "up" },
      { action: "SELL", option_type: "CE", direction: "down" },
    ]);
  });

  it("opens a saved plans strategy and describes every plan", () => {
    const saved = {
      kind: "plans" as const,
      underlying: "NIFTY" as const,
      plans: [
        {
          id: "P1",
          name: "Monday",
          underlying: "NIFTY" as const,
          days: ["MON" as const],
          entry: { mode: "time" as const, at: "09:30" },
          holding: { mode: "intraday" as const, exit: "15:00" },
          legs: [
            {
              id: "L1",
              action: "SELL" as const,
              option_type: "CE" as const,
              lots: 1,
              expiry: "current_week" as const,
            },
          ],
        },
        {
          id: "P2",
          name: "Friday",
          underlying: "SENSEX" as const,
          days: ["FRI" as const],
          entry: { mode: "time" as const, at: "10:00" },
          holding: { mode: "intraday" as const, exit: "15:00" },
          legs: [
            {
              id: "L1",
              action: "BUY" as const,
              option_type: "PE" as const,
              lots: 1,
              expiry: "current_week" as const,
            },
          ],
        },
      ],
    };
    const strategy = {
      id: "s1",
      name: "Week",
      description: null,
      config: saved,
      status: "draft",
      version: 1,
    };
    render(<StrategyBuilder catalog={catalog} strategy={strategy as never} />);
    expect(
      screen.getByText(/Monday · NIFTY: Enter at 09:30, Mon; exit at 15:00 the same day\./),
    ).toBeTruthy();
    expect(
      screen.getByText(/Friday · SENSEX: Enter at 10:00, Fri; exit at 15:00 the same day\./),
    ).toBeTruthy();
  });

  it("adds a plan for the days nobody trades, with its own instrument, entry and legs", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /Short strangle/ }));
    const week = within(screen.getByLabelText("Week at a glance"));
    expect(week.getAllByText("Short strangle")).toHaveLength(5); // the one plan trades every weekday

    fireEvent.click(screen.getByRole("button", { name: "Mon" })); // untick Monday on the first plan
    expect(week.getByText("no trade")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Add plan/ }));
    // the new plan takes the free day and is the one being edited
    expect(week.queryByText("no trade")).toBeNull();
    expect(week.getByText("Plan 2")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Plan 2/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "SENSEX" } });
    fireEvent.change(screen.getByLabelText("Plan name"), { target: { value: "Monday sensex" } });
    fireEvent.change(screen.getByLabelText("Enter"), { target: { value: "conditions" } });
    fireEvent.click(screen.getByRole("button", { name: "Opening range breakout / breakdown" }));
    fireEvent.click(screen.getByRole("button", { name: "Follow the tip (buy ATM CE / PE)" }));

    await flush();
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: {
        underlying: string;
        plans: {
          id: string;
          name: string;
          underlying: string;
          days: string[];
          entry: { mode: string };
          legs: { direction: string }[];
        }[];
      };
    };
    const [first, second] = saved.config.plans;
    expect(first).toMatchObject({
      name: "Short strangle",
      underlying: "NIFTY",
      days: ["TUE", "WED", "THU", "FRI"],
    });
    expect(second).toMatchObject({ id: "P2", name: "Monday sensex", underlying: "SENSEX", days: ["MON"] });
    expect(second!.entry.mode).toBe("conditions");
    expect(second!.legs.map((l) => l.direction)).toEqual(["up", "down"]);
    expect(first!.entry.mode).toBe("time");
  });

  it("duplicates and removes plans", () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /Short strangle/ }));
    fireEvent.click(screen.getByRole("button", { name: /Duplicate plan/ }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect((screen.getByLabelText("Plan name") as HTMLInputElement).value).toBe("Short strangle copy");
    fireEvent.click(screen.getByRole("button", { name: "Remove plan" }));
    expect(screen.getAllByRole("tab")).toHaveLength(1);
    expect((screen.getByRole("button", { name: "Remove plan" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the proven strategies' parameters instead of legs", () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /2h range breakout/ }));
    expect(legs()).toHaveLength(0);
    expect((screen.getByLabelText("Range until") as HTMLInputElement).value).toBe("11:15");
    fireEvent.change(screen.getByLabelText("Hedge wing"), { target: { value: "" } });
    expect(screen.getByText(/, unhedged\./)).toBeTruthy();
  });

  it("edits the SMC scalper's settings and saves them", async () => {
    render(<StrategyBuilder catalog={catalog} />);
    fireEvent.click(screen.getByRole("radio", { name: /SMC options scalper \(NIFTY\)/ }));
    expect(legs()).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "Timeframes" })).toBeTruthy();
    expect((screen.getByLabelText("Bias") as HTMLSelectElement).value).toBe("15");
    expect(screen.getByText(/TP1 1R, TP2 1.5R, TP3 2R, a tranche at each/)).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "1:3" }));
    expect(screen.getByText(/TP1 1R, TP2 2R, TP3 3R/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Strikes from ATM"), { target: { value: "-1" } });
    expect(screen.getByText(/Buy 1 lot \(65 qty\) of the ITM 1 call/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Displacement body"), { target: { value: "1.5" } });

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await flush();
    const saved = calls.find((c) => c.method === "POST" && c.path === "/v1/strategies")!.body as {
      config: {
        kind: string;
        risk: { rr: number };
        option: { strike: { offset: number } };
        rules: { displacement_atr: number };
      };
    };
    expect(saved.config.kind).toBe("smc_scalp");
    expect(saved.config.risk.rr).toBe(3);
    expect(saved.config.option.strike.offset).toBe(-1);
    expect(saved.config.rules.displacement_atr).toBe(1.5);
  });
});
