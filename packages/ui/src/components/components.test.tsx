import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

import { Button } from "./button";
import { Meter } from "./meter";
import { Pnl } from "./pnl";
import { StatusPill } from "./status-pill";

describe("ui components", () => {
  it("colours P&L by sign with theme tokens", () => {
    render(
      <>
        <Pnl value={1500} />
        <Pnl value={-20} />
        <Pnl value={0} />
      </>,
    );
    expect(screen.getByText("+₹1,500.00")).toHaveClass("text-profit");
    expect(screen.getByText("-₹20.00")).toHaveClass("text-loss");
    expect(screen.getByText("₹0.00")).toHaveClass("text-foreground");
  });

  it("status pill exposes its tone", () => {
    render(<StatusPill tone="danger">Not connected</StatusPill>);
    expect(screen.getByText("Not connected")).toHaveAttribute("data-tone", "danger");
  });

  it("buttons default to type=button so they never submit a form by accident", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "button");
  });

  it("meter shows usage and flags a full limit", () => {
    const { container } = render(
      <>
        <Meter label="Strategies" used={5} limit={5} />
        <Meter label="Brokers" used={1} limit={null} />
      </>,
    );
    expect(screen.getByText("5 / 5")).toBeInTheDocument();
    expect(screen.getByText("1 / Unlimited")).toBeInTheDocument();
    expect(container.querySelector(".bg-loss")).not.toBeNull();
  });
});

describe("icon buttons", () => {
  it("show their label as a tooltip on keyboard focus", async () => {
    render(
      <Button size="icon" aria-label="Delete strategy">
        x
      </Button>,
    );
    screen.getByRole("button", { name: "Delete strategy" }).focus();
    expect((await screen.findAllByText("Delete strategy")).length).toBeGreaterThan(0);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Delete strategy");
  });

  it("plain buttons get no tooltip", () => {
    render(<Button aria-label="Save">Save</Button>);
    screen.getByRole("button").focus();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
