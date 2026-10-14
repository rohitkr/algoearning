"use client";

import { Button } from "@algoearning/ui";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "./fields";

/** "Save as": a name for the new copy. The strategy being edited stays exactly as it was saved. */
export function SaveAsDialog({
  open,
  initialName,
  busy,
  error,
  onSave,
  onCancel,
}: {
  open: boolean;
  initialName: string;
  busy: boolean;
  error: string | null;
  onSave: (name: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(initialName);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- start from the suggested name each time it opens
    if (open) setName(initialName);
  }, [open, initialName]);
  return (
    <dialog
      ref={ref}
      aria-labelledby="saveas-title"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      onClick={(e) => e.target === ref.current && onCancel()}
      className="m-auto w-[min(28rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
    >
      {open && (
        <form
          className="flex flex-col gap-4 p-6"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && !busy) onSave(name.trim());
          }}
        >
          <div>
            <h2 id="saveas-title" className="text-lg font-semibold">
              Save as a new strategy
            </h2>
            <p className="mt-1.5 text-sm text-muted">
              Saves what is on screen as a new strategy. The original stays as it was last saved.
            </p>
          </div>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted">
            Name
            <input
              className={inputClass}
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              autoComplete="off"
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-loss">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Saving…" : "Save as new"}
            </Button>
          </div>
        </form>
      )}
    </dialog>
  );
}
