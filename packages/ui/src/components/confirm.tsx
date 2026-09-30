"use client";

import { type ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { Button } from "./button";

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmLabel?: string; // default "Confirm": say what will happen ("Stop strategy", "Delete")
  cancelLabel?: string; // default "Cancel"
  tone?: "danger" | "primary"; // danger for anything destructive or that touches real money
  /** The user must type this exactly before the confirm button works (e.g. a strategy's name). */
  typeToConfirm?: string;
}

type Ask = (options: ConfirmOptions) => Promise<boolean>;
const Ctx = createContext<Ask | null>(null);

/** Our own confirmation dialog instead of the browser's: `const confirm = useConfirm(); if (await confirm({...}))`.
 * Built on the native <dialog> (focus trap, Escape to cancel, inert page behind it), themed, and it names the action
 * on its button. Mount one ConfirmProvider near the root. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const [typed, setTyped] = useState("");

  const ask = useCallback<Ask>(
    (options) =>
      new Promise<boolean>((resolve) => {
        resolver.current?.(false); // a second question cancels the first
        resolver.current = resolve;
        setTyped("");
        setOpts(options);
      }),
    [],
  );

  useEffect(() => {
    const d = ref.current;
    if (opts && d && !d.open) d.showModal?.();
  }, [opts]);

  function finish(ok: boolean) {
    resolver.current?.(ok);
    resolver.current = null;
    ref.current?.close?.();
    setOpts(null);
  }

  const needsText = !!opts?.typeToConfirm;
  const ready = !needsText || typed.trim() === opts?.typeToConfirm?.trim();
  return (
    <Ctx.Provider value={ask}>
      {children}
      <dialog
        ref={ref}
        aria-labelledby="confirm-title"
        onCancel={(e) => {
          e.preventDefault();
          finish(false);
        }}
        onClick={(e) => e.target === ref.current && finish(false)}
        className="m-auto w-[min(28rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
      >
        {opts && (
          <form
            method="dialog"
            className="flex flex-col gap-4 p-6"
            onSubmit={(e) => {
              e.preventDefault();
              if (ready) finish(true);
            }}
          >
            <div>
              <h2 id="confirm-title" className="text-lg font-semibold">
                {opts.title}
              </h2>
              {opts.message && <div className="mt-1.5 text-sm text-muted">{opts.message}</div>}
            </div>
            {needsText && (
              <label className="flex flex-col gap-1 text-xs font-medium text-muted">
                Type <span className="font-semibold text-foreground">{opts.typeToConfirm}</span> to confirm
                <input
                  className="h-9 w-full rounded-lg border border-border bg-surface px-2.5 text-sm text-foreground outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  autoComplete="off"
                />
              </label>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => finish(false)}>
                {opts.cancelLabel ?? "Cancel"}
              </Button>
              <Button type="submit" variant={opts.tone === "danger" ? "danger" : "primary"} disabled={!ready}>
                {opts.confirmLabel ?? "Confirm"}
              </Button>
            </div>
          </form>
        )}
      </dialog>
    </Ctx.Provider>
  );
}

export function useConfirm(): Ask {
  const ask = useContext(Ctx);
  if (!ask) throw new Error("useConfirm needs a <ConfirmProvider> above it");
  return ask;
}
