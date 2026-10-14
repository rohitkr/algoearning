"use client";

import { Button } from "@algoearning/ui";
import { useEffect, useRef } from "react";

/** "You have unsaved changes": Discard them or cancel and keep editing. Native <dialog>: focus trap, Escape = keep editing. */
export function UnsavedDialog({
  open,
  onDiscard,
  onCancel,
}: {
  open: boolean;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby="unsaved-title"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      onClick={(e) => e.target === ref.current && onCancel()}
      className="m-auto w-[min(28rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
    >
      {open && (
        <div className="flex flex-col gap-4 p-6">
          <div>
            <h2 id="unsaved-title" className="text-lg font-semibold">
              Unsaved changes
            </h2>
            <p className="mt-1.5 text-sm text-muted">
              You changed this strategy and have not saved it. Leave and lose the changes, or cancel and keep
              editing.
            </p>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button variant="critical" onClick={onDiscard}>
              Discard
            </Button>
          </div>
        </div>
      )}
    </dialog>
  );
}
