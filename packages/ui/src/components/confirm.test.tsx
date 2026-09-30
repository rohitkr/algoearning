import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConfirmProvider, useConfirm } from "./confirm";

let answer: boolean | null = null;

function Trigger({ typeToConfirm }: { typeToConfirm?: string }) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={async () => {
        answer = await confirm({
          title: "Stop it?",
          message: "Positions are squared off.",
          confirmLabel: "Stop strategy",
          tone: "danger",
          typeToConfirm,
        });
      }}
    >
      ask
    </button>
  );
}

afterEach(cleanup);

beforeEach(() => {
  answer = null;
  // jsdom has <dialog> but not its modal methods
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
});

async function ask(typeToConfirm?: string) {
  render(
    <ConfirmProvider>
      <Trigger typeToConfirm={typeToConfirm} />
    </ConfirmProvider>,
  );
  await act(async () => fireEvent.click(screen.getByText("ask")));
}

describe("useConfirm", () => {
  it("resolves true on the named action and false on cancel", async () => {
    await ask();
    expect(screen.getByRole("heading", { name: "Stop it?" })).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Stop strategy" })));
    expect(answer).toBe(true);
    await act(async () => fireEvent.click(screen.getByText("ask")));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    expect(answer).toBe(false);
  });

  it("can require typing a name first", async () => {
    await ask("Nifty straddle");
    const go = screen.getByRole("button", { name: "Stop strategy" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: "Nifty straddle" } });
    expect(go.disabled).toBe(false);
  });
});
