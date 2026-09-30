import type { BrokerInfo } from "@algoearning/api-types";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AddBrokerDialog } from "./add-broker-dialog";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: async () => "token" }) }));

const CATALOG: BrokerInfo[] = [
  {
    code: "zerodha",
    name: "Zerodha",
    available: true,
    developer_console: "https://developers.kite.trade/apps",
    notes: "",
    redirect_url: null,
  },
];

beforeEach(() => {
  // jsdom has <dialog> but not its modal methods
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
  refresh.mockReset();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AddBrokerDialog", () => {
  it("closes cleanly after the broker is added (no error once the request returns)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ id: "b1" }), { status: 201 })),
    );
    const onClose = vi.fn();
    render(<AddBrokerDialog open onClose={onClose} catalog={CATALOG} />);
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "AB1234" } });
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "key-1234" } });
    fireEvent.change(screen.getByLabelText(/API secret/), { target: { value: "secret-1234" } });
    await act(async () => {
      fireEvent.submit(screen.getByLabelText("Client ID").closest("form")!);
    });
    expect(onClose).toHaveBeenCalled();
    expect(refresh).toHaveBeenCalled();
    expect(screen.queryByText(/Cannot read properties/)).toBeNull();
    expect((screen.getByLabelText("Client ID") as HTMLInputElement).value).toBe(""); // form reset for next time
  });

  it("has no password field, so the browser does not offer to save the API secret as a password", () => {
    const { container } = render(<AddBrokerDialog open onClose={vi.fn()} catalog={CATALOG} />);
    expect(container.querySelectorAll('input[type="password"]')).toHaveLength(0);
    const secret = screen.getByLabelText(/API secret/) as HTMLInputElement;
    expect(secret.className).toContain("[-webkit-text-security:disc]"); // masked while typing
    expect(secret.getAttribute("autocomplete")).toBe("off");
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(secret.className).not.toContain("text-security");
  });

  it("shows the API's message when adding fails", async () => {
    const body = {
      error: { code: "conflict", message: "this broker account is already added", details: null },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(body), { status: 409 })),
    );
    const onClose = vi.fn();
    render(<AddBrokerDialog open onClose={onClose} catalog={CATALOG} />);
    await act(async () => {
      fireEvent.submit(screen.getByLabelText("Client ID").closest("form")!);
    });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/already added/)).toBeTruthy();
  });
});
