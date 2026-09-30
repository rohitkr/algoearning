import type { Strategy } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DeployDialog } from "./deploy-dialog";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const S = { id: "s1", name: "Nifty straddle" } as Strategy;

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

describe("DeployDialog", () => {
  it("deploys on paper with the multiplier and opens the run", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: "r1" }), { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    render(<DeployDialog strategy={S} open onClose={vi.fn()} />);
    expect((screen.getByLabelText("Live (coming soon)") as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Multiplier/), { target: { value: "3" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Deploy on paper" })));
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/strategies\/s1\/deploy$/);
    expect(JSON.parse(String(init.body))).toEqual({ mode: "paper", multiplier: 3 });
    expect(push).toHaveBeenCalledWith("/runs/r1");
  });

  it("explains a plan limit", async () => {
    const body = {
      error: { code: "plan_limit", message: "Your Free plan allows 1 (strategies running at once)" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status: 403 })),
    );
    render(<DeployDialog strategy={S} open onClose={vi.fn()} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Deploy on paper" })));
    expect(screen.getByRole("alert").textContent).toContain("allows 1");
    expect(screen.getByRole("link", { name: "See plans" })).toBeTruthy();
  });
});
