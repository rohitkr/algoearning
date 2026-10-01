/** Shown on sign-in and in place of sign-up while the app is an internal alpha: new accounts are refused. */
export const CLOSED_ALPHA = "Internal Alpha Test Environment. Closed to the public.";

export function ClosedAlphaBanner() {
  return (
    <p
      role="status"
      className="rounded-lg border border-border bg-surface px-4 py-3 text-center text-sm text-muted"
    >
      {CLOSED_ALPHA}
    </p>
  );
}
