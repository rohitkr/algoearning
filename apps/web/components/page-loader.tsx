import { Loader2 } from "lucide-react";

/** Shown in the main body while the next page loads, so a click on a tab visibly does something. */
export function PageLoader() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex min-h-[40vh] items-center justify-center gap-2 text-muted"
    >
      <Loader2 className="size-5 animate-spin" aria-hidden />
      <span className="text-sm">Loading…</span>
    </div>
  );
}
