import type { SignalSetup, SignalSource } from "@algoearning/api-types";
import { ConfirmProvider } from "@algoearning/ui";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignalSources } from "./signal-sources";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const SETUP: SignalSetup = { platform_app: true, max_sources: 1, used: 1 };
const base: SignalSource = {
  id: "s1",
  label: "VIP",
  status: "code_sent",
  status_detail: "code sent to your Telegram app",
  next_step: "code",
  flood_until: null,
  phone_masked: "+91 98•••••210",
  api_id: null,
  platform_app: true,
  account_name: null,
  chat_id: null,
  chat_title: null,
  chat_kind: null,
  connected_at: null,
  created_at: "2026-10-08T10:00:00Z",
  reader_state: "off",
  reader_detail: null,
  last_message_at: null,
};

type Call = { method: string; path: string; body: unknown };
let calls: Call[] = [];
let reply: (path: string) => unknown = () => ({});

beforeEach(() => {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
  calls = [];
  refresh.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      calls.push({
        method: init.method ?? "GET",
        path,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      return new Response(JSON.stringify(reply(path)), { status: 200 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const show = (sources: SignalSource[], setup: SignalSetup = SETUP) =>
  render(
    <ConfirmProvider>
      <SignalSources sources={sources} setup={setup} />
    </ConfirmProvider>,
  );

describe("SignalSources", () => {
  it("starts a login with the platform's app and sends the code", async () => {
    show([], { platform_app: true, max_sources: 1, used: 0 });
    expect(screen.getByText(/Telegram has no read-only login/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/Telegram phone number/), {
      target: { value: "+91 98123 45210" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send login code" })));
    expect(calls[0]).toEqual({
      method: "POST",
      path: "/v1/signal-sources",
      body: { phone: "+91 98123 45210", label: null },
    });
  });

  it("asks for the code, then the chat; nothing secret is shown", async () => {
    const { rerender } = show([base]);
    expect(screen.getByText("Waiting for the login code")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Login code"), { target: { value: "57931" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Log in" })));
    expect(calls.at(-1)).toEqual({
      method: "POST",
      path: "/v1/signal-sources/s1/code",
      body: { code: "57931" },
    });
    expect(refresh).toHaveBeenCalled();

    const connected: SignalSource = { ...base, status: "connected", status_detail: null, next_step: "chat",
      account_name: "Rohit (@rk)" }; // prettier-ignore
    reply = (p) =>
      p.endsWith("/chats")
        ? [{ id: -1001234567890, title: "Nifty Sensex VIP setups", kind: "channel", username: "vipsetups" }]
        : {};
    rerender(
      <ConfirmProvider>
        <SignalSources sources={[connected]} setup={SETUP} />
      </ConfirmProvider>,
    );
    expect(screen.getByText(/Rohit \(@rk\) · \+91 98•••••210/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Choose the tips channel" })));
    fireEvent.change(screen.getByLabelText("Channel or group to read"), {
      target: { value: "-1001234567890" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Read this chat" })));
    expect(calls.at(-1)).toEqual({
      method: "PUT",
      path: "/v1/signal-sources/s1/chat",
      body: { chat_id: -1001234567890 },
    });
  });

  it("asks for the 2-step password and offers to reconnect an expired session", () => {
    show([{ ...base, status: "password_needed", next_step: "password" }]);
    expect(screen.getByLabelText("2-step verification password")).toBeTruthy();
    cleanup();
    show([{ ...base, status: "needs_reconnect", next_step: "login", status_detail: "reconnect Telegram" }]);
    expect(screen.getByText("Reconnect Telegram")).toBeTruthy();
    expect(screen.getByText(/Leave empty to use \+91 98•••••210/)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: /Add a Telegram source/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
