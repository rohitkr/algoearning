import type { RulesConfig, StrategyCatalog, StrategyLeg } from "@algoearning/api-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import fixture from "./catalog.fixture.json";
import { DayLegs } from "./day-legs";

const catalog = fixture as StrategyCatalog;
const inst = catalog.instruments.find((i) => i.code === "NIFTY");
const BASE = {
  kind: "rules", underlying: "NIFTY", legs: [{ id: "L1", action: "SELL", option_type: "CE", lots: 1, expiry: "current_week",
  strike: { mode: "atm", offset: 0, premium: null, points: null }, direction: "always" }],
} as unknown as RulesConfig; // prettier-ignore

afterEach(cleanup);

/** The config lives in the builder: a small stand-in that keeps it, like the real parent does. */
function setup(config: RulesConfig = BASE) {
  const onChange = vi.fn();
  function Harness() {
    const [c, setC] = useState(config);
    return (
      <DayLegs
        config={c}
        catalog={catalog}
        inst={inst}
        errs={{}}
        warns={{}}
        onChange={(n) => {
          onChange(n);
          setC(n);
        }}
      />
    );
  }
  render(<Harness />);
  return onChange;
}
const last = (f: ReturnType<typeof vi.fn>) => f.mock.calls.at(-1)![0] as RulesConfig;
const legsOf = (c: RulesConfig, d: string) => (c.day_legs as Record<string, StrategyLeg[]>)[d]!;

describe("DayLegs", () => {
  it("is off until switched on, and off again removes every weekday's legs", () => {
    const onChange = setup();
    expect(screen.queryByRole("tablist")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    expect(screen.getByRole("tablist")).toBeTruthy();
    expect(last(onChange).day_legs).toEqual({});
  });

  it("switching off removes every weekday's legs", () => {
    const withMon = {
      ...BASE,
      day_legs: { MON: [{ ...BASE.legs[0]!, id: "MON1" }] },
    } as unknown as RulesConfig;
    const onChange = setup(withMon);
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    expect(last(onChange).day_legs).toBeNull();
  });

  it("builds Monday as a strangle at about ₹60 premium on both sides", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    fireEvent.click(screen.getByText("Add MON legs"));
    const legs = legsOf(last(onChange), "MON");
    expect(legs.map((l) => [l.id, l.action, l.option_type, l.strike?.mode, l.strike?.premium])).toEqual([
      ["MON1", "SELL", "CE", "premium", 60],
      ["MON2", "SELL", "PE", "premium", 60],
    ]);
  });

  it("offers every strike rule the leg editor has: premium at least, OTM strikes, points", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    fireEvent.change(screen.getByLabelText("Sell strike"), { target: { value: "premium_gte" } });
    fireEvent.click(screen.getByText("Add MON legs"));
    expect(legsOf(last(onChange), "MON").map((l) => [l.strike?.mode, l.strike?.premium])).toEqual([
      ["premium_gte", 60], ["premium_gte", 60],
    ]); // prettier-ignore
    fireEvent.click(screen.getByRole("tab", { name: "TUE" }));
    fireEvent.change(screen.getByLabelText("Sell strike"), { target: { value: "3" } });
    fireEvent.click(screen.getByText("Add TUE legs"));
    expect(legsOf(last(onChange), "TUE").map((l) => [l.strike?.mode, l.strike?.offset])).toEqual([
      ["atm", 3], ["atm", 3],
    ]); // prettier-ignore
  });

  it("builds an iron condor and an iron fly with their wings", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    fireEvent.click(screen.getByRole("tab", { name: "TUE" }));
    fireEvent.change(screen.getByLabelText("Trade"), { target: { value: "condor" } });
    fireEvent.click(screen.getByText("Add TUE legs"));
    const condor = legsOf(last(onChange), "TUE");
    expect(condor.map((l) => [l.action, l.option_type, l.strike?.premium])).toEqual([
      ["SELL", "CE", 60], ["SELL", "PE", 60], ["BUY", "CE", 10], ["BUY", "PE", 10],
    ]); // prettier-ignore
    fireEvent.click(screen.getByRole("tab", { name: "WED" }));
    fireEvent.change(screen.getByLabelText("Trade"), { target: { value: "fly" } });
    fireEvent.change(screen.getByLabelText("Buy wing strike"), { target: { value: "points" } });
    fireEvent.click(screen.getByText("Add WED legs"));
    const fly = legsOf(last(onChange), "WED");
    expect(fly.map((l) => [l.action, l.option_type, l.strike?.mode])).toEqual([
      ["SELL", "CE", "atm"], ["SELL", "PE", "atm"], ["BUY", "CE", "points"], ["BUY", "PE", "points"],
    ]); // prettier-ignore
  });

  it("copies a day's legs to the other weekdays with ids that stay unique", () => {
    const withMon = {
      ...BASE,
      day_legs: { MON: [{ ...BASE.legs[0]!, id: "MON1" }] },
    } as unknown as RulesConfig;
    const onChange = setup(withMon);
    fireEvent.click(screen.getByText("Copy to the other weekdays"));
    const c = last(onChange);
    expect(Object.keys(c.day_legs!).sort()).toEqual(["FRI", "MON", "THU", "TUE", "WED"]);
    expect(legsOf(c, "TUE")[0]!.id).toBe("TUE1");
    const ids = Object.values(c.day_legs!)
      .flat()
      .map((l) => l.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
