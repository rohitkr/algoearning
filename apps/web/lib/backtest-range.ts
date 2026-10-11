import type { HistoryCoverage } from "@algoearning/api-types";

const KEY = "ae.backtest.range";

export type TestSettings = { from: string; to: string; multiplier: number; slippage: number };

/** The date `months` calendar months before `iso` (clamped to the month's last day). */
export function monthsBefore(iso: string, months: number): string {
  const [y = 0, m = 1, d = 1] = iso.split("-").map(Number);
  const last = new Date(Date.UTC(y, m - 1 - months + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m - 1 - months, Math.min(d, last))).toISOString().slice(0, 10);
}

/** Last 3 months of the stored data (the option range, else the index range), never before the data starts. */
export function defaultRange(c: HistoryCoverage | null | undefined): { from: string; to: string } {
  const to = c?.option_to ?? c?.index_to ?? "";
  const first = c?.option_from ?? c?.index_from ?? "";
  if (!to) return { from: "", to: "" };
  const back = monthsBefore(to, 3);
  return { from: first && back < first ? first : back, to };
}

/** The dates, multiplier and slippage last used, if any (browser storage can be missing or blocked). */
export function loadSettings(): Partial<TestSettings> {
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) ?? "null") as Partial<TestSettings> | null;
    if (!raw || typeof raw !== "object") return {};
    const date = (v: unknown) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined);
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
    return {
      from: date(raw.from),
      to: date(raw.to),
      multiplier: num(raw.multiplier),
      slippage: num(raw.slippage),
    };
  } catch {
    return {};
  }
}

export function saveSettings(s: TestSettings): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // storage unavailable: the dates just are not remembered
  }
}
