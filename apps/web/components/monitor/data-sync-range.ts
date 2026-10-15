// The date range of Monitor > Data sync (plain functions: used by the server page and the client panel).

export const RANGE_KEY = "ae-data-sync-range";
export const istDay = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }); // yyyy-mm-dd

/** What the page shows when nobody chose dates: this year's 1 January to today (IST). */
export function defaultRange(now = new Date()): { start: string; end: string } {
  const today = istDay.format(now);
  return { start: `${today.slice(0, 4)}-01-01`, end: today };
}

/** The dates the admin chose earlier, if that was today: they are forgotten at midnight (IST). */
export function rememberedRange(raw: string | null, now = new Date()): { start: string; end: string } | null {
  try {
    const v = JSON.parse(raw ?? "null") as { day?: string; start?: string; end?: string } | null;
    return v?.day === istDay.format(now) && v.start && v.end ? { start: v.start, end: v.end } : null;
  } catch {
    return null;
  }
}
