import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { FeatureEditor } from "./feature-editor";

const CATALOG = [
  { key: "live_trading", kind: "flag" as const, label: "Live trading" },
  { key: "max_broker_accounts", kind: "limit" as const, label: "Broker accounts" },
];

type Values = Record<string, boolean | number | null>;
let last: Values = {};
const report = (v: Values) => {
  last = v;
};

function Harness({ start }: { start: Values }) {
  const [v, setV] = useState(start);
  return (
    <FeatureEditor
      catalog={CATALOG}
      value={v}
      onChange={(n) => {
        setV(n);
        report(n);
      }}
      mode="override"
      planValues={{ live_trading: false, max_broker_accounts: 1 }}
    />
  );
}

afterEach(cleanup);

describe("FeatureEditor (overrides)", () => {
  it("only stores the features an admin overrides", () => {
    render(<Harness start={{}} />);
    expect(screen.getByText(/plan: Off/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Broker accounts/), { target: { value: "number" } });
    expect(last).toEqual({ max_broker_accounts: 1 }); // starts from the plan's value
    fireEvent.change(screen.getByLabelText("Broker accounts limit"), { target: { value: "5" } });
    expect(last).toEqual({ max_broker_accounts: 5 });
    fireEvent.change(screen.getByLabelText(/Broker accounts/, { selector: "select" }), {
      target: { value: "unlimited" },
    });
    expect(last).toEqual({ max_broker_accounts: null });
    fireEvent.change(screen.getByLabelText(/Live trading/), { target: { value: "on" } });
    fireEvent.change(screen.getByLabelText(/Broker accounts/, { selector: "select" }), {
      target: { value: "plan" },
    });
    expect(last).toEqual({ live_trading: true });
  });
});
