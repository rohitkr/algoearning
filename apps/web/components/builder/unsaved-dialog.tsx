"use client";

import { Button } from "@algoearning/ui";
import { useEffect, useRef } from "react";

/** "You have unsaved changes": Save, Discard or keep editing. Native <dialog>: focus trap, Escape = keep editing. */
export function UnsavedDialog({
  open,
  saving,
  onSave,
  onDiscard,
  onCancel,
}: {
  open: boolean;
  saving: boolean;
  onSave: () => void;
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
              You changed this strategy and have not saved it. Save it before leaving, or discard the changes.
            </p>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
            <Button variant="critical" onClick={onDiscard} disabled={saving}>
              Discard
            </Button>
            <Button onClick={onSave} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </dialog>
  );
}
