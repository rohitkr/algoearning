import type { SignalMessage, SignalSource, TipSignal } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignalFeed } from "./signal-feed";

vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const SOURCE = {
  id: "s1",
  chat_id: -100123,
  chat_title: "Nifty Sensex VIP setups",
  reader_state: "listening",
  reader_detail: null,
  last_message_at: "2026-10-08T05:30:00Z",
} as SignalSource;
const SIGNAL: TipSignal = {
  id: 1, date: "2026-10-08T04:45:00Z", index: "NIFTY", strike: 22450, option_type: "CE", action: "BUY",
  direction: "BULLISH", entry_low: 150, entry_high: 154, stop_loss: 135, targets: [169, 184, 204],
  targets_done: [1], rationale: "Reversal", valid_for: "Intraday Only", intraday: true, status: "T1",
  last_price: 170, complete: true, message_ids: [1, 2, 3, 4],
}; // prettier-ignore
const msg = (msg_id: number, kind: SignalMessage["kind"], text: string, signal_id: number | null = 1): SignalMessage => ({
  msg_id, date: "2026-10-08T04:45:00Z", edit_date: null, text, reply_to: null, has_media: false, kind, data: {},
  signal_id, overridden: false,
}); // prettier-ignore
const MESSAGES = [
  msg(5, "NOISE", "Good afternoon traders", null),
  msg(4, "TARGET", "🎯 Target 1 done"),
  msg(3, "TICK", "₹157 🔥🔥🔥"),
  msg(2, "DETAILS", "🎯 TP 1: ₹169 …"),
  msg(1, "SIGNAL", "🟢 BUY NIFTY 22450 CE"),
];

let calls: { method: string; path: string; body: unknown }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      calls.push({
        method: init.method ?? "GET",
        path,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      const body = path.endsWith("/signals") ? [SIGNAL] : path.endsWith("/messages") ? MESSAGES : MESSAGES[0];
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SignalFeed", () => {
  it("shows signals and the chat as read; ticks hidden unless their signal is picked", async () => {
    render(<SignalFeed source={SOURCE} />);
    expect(await screen.findByText("▲ Bullish")).toBeTruthy();
    expect(screen.getByText("BUY NIFTY 22450 CE")).toBeTruthy();
    expect(screen.getByText("● Live")).toBeTruthy();
    expect(screen.getByText("169").className).toContain("text-profit"); // target 1 hit
    expect(screen.queryByText("₹157 🔥🔥🔥")).toBeNull(); // a price tick, hidden
    expect(screen.getByText("Good afternoon traders")).toBeTruthy();

    await act(async () => fireEvent.click(screen.getByText("BUY NIFTY 22450 CE")));
    expect(screen.getByText("₹157 🔥🔥🔥")).toBeTruthy(); // the picked signal's ticks show
    expect(screen.getByText("🟢 BUY NIFTY 22450 CE").closest("li")?.className).toContain("bg-primary/10");
  });

  it("corrects how a message was read", async () => {
    render(<SignalFeed source={SOURCE} />);
    await screen.findByText("Good afternoon traders");
    await act(async () =>
      fireEvent.change(screen.getByLabelText("Correct how message 5 was read"), {
        target: { value: "ADVISORY" },
      }),
    );
    expect(calls.find((c) => c.method === "PUT")).toEqual({
      method: "PUT",
      path: "/v1/signal-sources/s1/messages/5/kind",
      body: { kind: "ADVISORY" },
    });
  });
});
