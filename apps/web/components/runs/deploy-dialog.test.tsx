import type { LiveStatus, Strategy } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DeployDialog } from "./deploy-dialog";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const S = { id: "s1", name: "Nifty straddle" } as Strategy;
const LOCKED: LiveStatus = {
  plan_allows: false,
  unlocked: false,
  brokers: [],
  can_dry_run: false,
  can_go_live: false,
  reasons: ["your Free plan does not include live trading"],
};
const READY: LiveStatus = {
  plan_allows: true,
  unlocked: true,
  brokers: [{ id: "b1", client_id: "AB1234", label: null, connected: true, engine_enabled: true }],
  can_dry_run: true,
  can_go_live: true,
  reasons: [],
};

function api(
  live: LiveStatus,
  deploy: () => Response = () => new Response(JSON.stringify({ id: "r1" }), { status: 201 }),
) {
  const fetch = vi.fn(async (url: string) =>
    url.endsWith("/v1/me/live") ? new Response(JSON.stringify(live), { status: 200 }) : deploy(),
  );
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

async function open() {
  await act(async () => {
    render(<DeployDialog strategy={S} open onClose={vi.fn()} />);
  });
}

beforeEach(() => {
  // jsdom has <dialog> but not its modal methods
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  push.mockReset();
});

const body = (fetch: ReturnType<typeof api>) => {
  const call = fetch.mock.calls.find(([u]) => String(u).includes("/deploy")) as unknown as [
    string,
    RequestInit,
  ];
  return JSON.parse(String(call[1].body));
};

describe("DeployDialog", () => {
  it("deploys on paper with the multiplier and opens the run", async () => {
    const fetch = api(LOCKED);
    await open();
    expect((screen.getByLabelText(/Live, real orders/) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("your Free plan does not include live trading")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Multiplier/), { target: { value: "3" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Deploy on paper" })));
    expect(body(fetch)).toEqual({ mode: "paper", multiplier: 3 });
    expect(push).toHaveBeenCalledWith("/runs/r1");
  });

  it("needs the strategy's name typed before real orders", async () => {
    const fetch = api(READY);
    await open();
    fireEvent.click(screen.getByLabelText(/Live, real orders/));
    const go = screen.getByRole("button", { name: "Deploy with real orders" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: "Nifty straddle" } });
    expect(go.disabled).toBe(false);
    await act(async () => fireEvent.click(go));
    expect(body(fetch)).toEqual({
      mode: "live",
      multiplier: 1,
      broker_account_id: "b1",
      confirm: "Nifty straddle",
    });
  });

  it("runs the pre-flight checks before real orders", async () => {
    const fetch = vi.fn(async (url: string) => {
      if (url.endsWith("/v1/me/live")) return new Response(JSON.stringify(READY), { status: 200 });
      const out = { ok: false, checks: [{ name: "Engine", ok: false, detail: "not running" }] };
      return new Response(JSON.stringify(out), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    await open();
    fireEvent.click(screen.getByLabelText(/Live, real orders/));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Check everything is ready" })));
    expect(screen.getByLabelText("Pre-flight checks").textContent).toContain("✗ Engine: not running");
    expect(String(fetch.mock.calls.at(-1)![0])).toContain("/preflight?broker_account_id=b1");
  });

  it("starts a dry run without confirmation", async () => {
    const fetch = api(READY);
    await open();
    fireEvent.click(screen.getByLabelText(/Live, dry run/));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Start dry run" })));
    expect(body(fetch)).toEqual({ mode: "live", dry_run: true, multiplier: 1 });
  });

  it("explains a plan limit", async () => {
    const err = {
      error: { code: "plan_limit", message: "Your Free plan allows 1 (strategies running at once)" },
    };
    api(LOCKED, () => new Response(JSON.stringify(err), { status: 403 }));
    await open();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Deploy on paper" })));
    expect(screen.getByRole("alert").textContent).toContain("allows 1");
    expect(screen.getByRole("link", { name: "See plans" })).toBeTruthy();
  });
});
