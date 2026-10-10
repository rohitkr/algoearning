import type { RulesConfig, StrategyCatalog, StrategyLeg } from "@algoearning/api-types";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  it("switching on gives an empty set and off removes every weekday's legs", () => {
    const onChange = setup();
    expect(screen.queryByRole("tablist")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    expect(screen.getByRole("tablist")).toBeTruthy();
    expect(last(onChange).day_legs).toEqual({});
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    expect(last(onChange).day_legs).toBeNull();
  });

  it("a weekday is built leg by leg, any shape: an iron condor with a bought ATM CE and PE", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByText("Add leg"));
    let legs = legsOf(last(onChange), "MON");
    expect(legs.map((l) => l.id)).toEqual(["MON1", "MON2", "MON3", "MON4"]);
    // leg 2 -> PE; legs 3 and 4 -> BUY CE / BUY PE, which are ATM by default
    const flip = (leg: number, what: "action" | "option type", label: string) =>
      fireEvent.click(
        within(screen.getByRole("radiogroup", { name: `Leg ${leg} ${what}` })).getByText(label),
      );
    flip(2, "option type", "PE");
    flip(3, "action", "BUY");
    flip(4, "action", "BUY");
    flip(4, "option type", "PE");
    // leg 1 sells near a premium
    fireEvent.change(screen.getAllByLabelText("Strike")[0]!, { target: { value: "premium" } });
    legs = legsOf(last(onChange), "MON");
    expect(legs.map((l) => [l.action, l.option_type, l.strike?.mode])).toEqual([
      ["SELL", "CE", "premium"], ["SELL", "PE", "atm"], ["BUY", "CE", "atm"], ["BUY", "PE", "atm"],
    ]); // prettier-ignore
  });

  it("each weekday has its own list, and a weekday's last leg removed means it uses the default legs again", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText(/Trade different legs on different weekdays/));
    fireEvent.click(screen.getByText("Add leg"));
    fireEvent.click(screen.getByRole("tab", { name: "TUE" }));
    expect(screen.getByText(/Tuesday has no legs of its own: it trades the default legs above/)).toBeTruthy();
    fireEvent.click(screen.getByText("Add leg"));
    expect(Object.keys(last(onChange).day_legs!).sort()).toEqual(["MON", "TUE"]);
    expect(legsOf(last(onChange), "TUE")[0]!.id).toBe("TUE1");
    fireEvent.click(screen.getByLabelText("Remove leg 1"));
    expect(Object.keys(last(onChange).day_legs!)).toEqual(["MON"]);
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

  it("a weekday can start from a copy of the default legs or of another weekday", () => {
    const withMon = {
      ...BASE,
      day_legs: { MON: [{ ...BASE.legs[0]!, id: "MON1" }] },
    } as unknown as RulesConfig;
    const onChange = setup(withMon);
    fireEvent.click(screen.getByRole("tab", { name: "WED" }));
    fireEvent.click(screen.getByText("Copy MON"));
    expect(legsOf(last(onChange), "WED").map((l) => l.id)).toEqual(["WED1"]);
    fireEvent.click(screen.getByRole("tab", { name: "THU" }));
    fireEvent.click(screen.getByText("Copy the default legs"));
    expect(legsOf(last(onChange), "THU").map((l) => l.id)).toEqual(["THU1"]);
  });
});
