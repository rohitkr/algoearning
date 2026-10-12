import { useState } from "react";

/**
 * The last value a switchable setting had while it was on. Unticking a setting clears it (null) but the person's numbers
 * should still be there when they tick it again: use `remembered ?? default` when switching it on.
 */
export function useRemembered<T>(value: T | null | undefined): T | undefined {
  const [last, setLast] = useState<T | undefined>(value ?? undefined);
  if (value != null && value !== last) setLast(value); // adjusting state while rendering: React re-renders at once
  return last;
}
