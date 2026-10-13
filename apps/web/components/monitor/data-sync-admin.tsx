"use client";

import type { DataSyncCoverage, FeedLogin, SyncDay, SyncJobOut } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Button, Card, CardTitle, StatusPill, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { inputClass } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

import { fmtDateTime } from "./bits";

const DAILY_LIMIT = 5000; // Breeze's daily call limit, shared with the live price feed
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];
const monthTitle = new Intl.DateTimeFormat("en-IN", { timeZone: "UTC", month: "short", year: "numeric" });
const dayTitle = new Intl.DateTimeFormat("en-IN", {
  timeZone: "UTC",
  weekday: "short",
  day: "numeric",
  month: "short",
});
const at = (iso: string) => new Date(`${iso}T00:00:00Z`);

const STATUS_TEXT: Record<SyncDay["status"], string> = {
  ok: "complete",
  partial: "partly stored",
  missing: "missing",
  closed: "market closed",
};
const STATUS_CLASS: Record<SyncDay["status"], string> = {
  ok: "bg-profit",
  partial: "bg-warning",
  missing: "bg-loss",
  closed: "bg-surface-2",
};

export const isActive = (j: Pick<SyncJobOut, "status">) => j.status === "pending" || j.status === "running";

/** What the numbers in a day's tooltip say. */
export function dayLabel(d: SyncDay): string {
  if (d.status === "closed") {
    const weekend = [0, 6].includes(at(d.day).getUTCDay());
    return `${dayTitle.format(at(d.day))}: ${weekend ? "weekend, market closed" : "market closed (holiday)"}`;
  }
  const head = `${dayTitle.format(at(d.day))}: ${STATUS_TEXT[d.status]}`;
  const parts = [`index ${d.index_bars} of 375 candles`];
  if (d.options_expected)
    parts.push(
      `options ${d.options_complete} complete, ${d.options_partial} partial, ${d.options_missing} missing of ${d.options_expected}`,
    );
  if (d.closing_missing) parts.push(`${d.closing_missing} without 15:30 to 15:39 candles`);
  return `${head} (${parts.join("; ")})`;
}

/** Months from the first to the last day, each with its days' status (Sunday first, like a wall calendar). */
export function monthsOf(days: readonly SyncDay[], start: string, end: string) {
  const by = new Map(days.map((d) => [d.day, d]));
  const out: {
    key: string;
    title: string;
    lead: number;
    cells: { date: string; day: number; d?: SyncDay }[];
  }[] = [];
  const last = at(end);
  for (
    let m = new Date(Date.UTC(at(start).getUTCFullYear(), at(start).getUTCMonth(), 1));
    m <= last;
    m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))
  ) {
    const count = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0)).getUTCDate();
    out.push({
      key: m.toISOString().slice(0, 7),
      title: monthTitle.format(m),
      lead: m.getUTCDay(),
      cells: Array.from({ length: count }, (_, i) => {
        const date = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), i + 1))
          .toISOString()
          .slice(0, 10);
        return { date, day: i + 1, d: by.get(date) };
      }),
    });
  }
  return out;
}

/** "3 min", "1 h 05 min": how long a download still needs at the pace it has kept so far. */
export function eta(done: number, total: number, startedAt: string | null, now = Date.now()): string | null {
  if (!startedAt || done <= 0 || total <= done) return null;
  const perItem = (now - new Date(startedAt).getTime()) / done;
  const mins = Math.max(1, Math.round((perItem * (total - done)) / 60_000));
  return mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)} h ${String(mins % 60).padStart(2, "0")} min`;
}

const PHASE: Record<string, string> = {
  queued: "Waiting for the worker",
  starting: "Starting",
  index: "Index candles",
  options: "Option contracts",
  closing: "Closing session (15:30 to 15:39)",
};

function Tile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "warn" | "bad";
}) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p
        className={cn(
          "mt-1 text-2xl font-semibold tabular-nums",
          tone === "bad" && "text-loss",
          tone === "warn" && "text-warning",
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Card>
  );
}

function JobProgress({ job, onStop, busy }: { job: SyncJobOut; onStop: () => void; busy: boolean }) {
  const p = job.progress as { phase?: string; done?: number; total?: number; calls?: number; rows?: number };
  const done = p.done ?? 0;
  const total = p.total ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const left = eta(done, total, job.started_at);
  return (
    <Card className="flex flex-col gap-3" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>
          {job.mode === "closing" ? "Closing-session download" : "Download"} · {job.underlying}
        </CardTitle>
        <StatusPill tone={job.status === "running" ? "info" : "neutral"}>
          {job.cancel_requested ? "Stopping…" : job.status === "pending" ? "Queued" : "Running"}
        </StatusPill>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Download progress"
        className="h-3 overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-sm">
        {PHASE[p.phase ?? ""] ?? p.phase}:{" "}
        <span className="font-medium tabular-nums">{formatNumber(done)}</span> of{" "}
        <span className="tabular-nums">{formatNumber(total)}</span> ({pct}%) ·{" "}
        <span className="tabular-nums">{formatNumber(total - done)}</span> pending
        {left && <> · about {left} left</>}
      </p>
      <p className="text-xs text-muted">
        {formatNumber(p.calls ?? 0)} provider calls so far · {formatNumber(p.rows ?? 0)} candles stored ·
        started {fmtDateTime(job.started_at ?? job.created_at)}. You can leave this page: the download keeps
        running.
      </p>
      <div>
        <Button variant="secondary" size="sm" onClick={onStop} disabled={busy || job.cancel_requested}>
          Stop
        </Button>
      </div>
    </Card>
  );
}

function jobSummary(j: SyncJobOut): string {
  if (j.error) return j.error;
  const r = (j.result ?? {}) as {
    stopped?: string | null;
    rows?: number;
    calls_made?: number;
    notes?: string[];
  };
  const bits = [`${formatNumber(r.rows ?? 0)} candles, ${formatNumber(r.calls_made ?? 0)} calls`];
  if (r.stopped) bits.push(`stopped: ${r.stopped}`);
  if (r.notes?.length) bits.push(...r.notes);
  return bits.join(" · ");
}

const JOB_TONE = {
  done: "success",
  stopped: "warning",
  failed: "danger",
  running: "info",
  pending: "neutral",
} as const;

export function DataSyncPanel({
  coverage: c,
  initialJobs,
  logins,
  callsToday,
}: {
  coverage: DataSyncCoverage;
  initialJobs: SyncJobOut[];
  logins: FeedLogin[];
  callsToday: number | null;
}) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [jobs, setJobs] = useState(initialJobs);
  const [provider, setProvider] = useState<"auto" | "breeze" | "kite">("auto");
  const [from, setFrom] = useState(c.start);
  const [to, setTo] = useState(c.end);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tip, setTip] = useState<string | null>(null);
  const active = jobs.find(isActive);
  const wasActive = useRef(Boolean(active));

  // while a download runs, follow it every two seconds; when it ends, reload the stored-history picture
  useEffect(() => {
    if (!active) {
      if (wasActive.current) router.refresh();
      wasActive.current = false;
      return;
    }
    wasActive.current = true;
    const t = setInterval(async () => {
      try {
        setJobs(await apiRequest<SyncJobOut[]>("GET", "/v1/admin/data-sync/jobs", undefined, getToken));
      } catch {
        // a missed poll is fine: the next one catches up
      }
    }, 2000);
    return () => clearInterval(t);
  }, [active, getToken, router]);

  const effective = provider === "auto" ? c.default_provider : provider;
  const login = logins.find((l) => l.provider === effective);
  const sessionOk = login?.session_expires_at ? new Date(login.session_expires_at) > new Date() : false;
  const why = active
    ? "A download is already running."
    : effective === "none"
      ? "No price provider is set up (Breeze or Kite keys in .env)."
      : !login
        ? `${effective === "breeze" ? "Breeze" : "Kite"} is not set up in .env.`
        : !sessionOk
          ? `Log in to ${login.name} first (Monitor > Market data): sessions last a day.`
          : null;

  async function start(mode: "fill" | "closing") {
    setBusy(true);
    setError(null);
    try {
      const job = await apiRequest<SyncJobOut>(
        "POST",
        "/v1/admin/data-sync/jobs",
        { underlying: c.underlying, mode, provider, start_date: mode === "fill" ? from : null, end_date: to },
        getToken,
      );
      setJobs((j) => [job, ...j]);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function stop(id: string) {
    setBusy(true);
    try {
      const job = await apiRequest<SyncJobOut>(
        "POST",
        `/v1/admin/data-sync/jobs/${id}/cancel`,
        undefined,
        getToken,
      );
      setJobs((all) => all.map((j) => (j.id === id ? job : j)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function go(params: Record<string, string>) {
    const q = new URLSearchParams({ underlying: c.underlying, start: from, end: to, ...params });
    router.push(`/monitor/data-sync?${q}`);
  }

  const trading = c.days.filter((d) => d.status !== "closed");
  const ok = trading.filter((d) => d.status === "ok").length;
  const problems = trading.filter((d) => d.status !== "ok");
  const months = useMemo(() => monthsOf(c.days, c.start, c.end), [c.days, c.start, c.end]);
  const optionDays = trading.reduce((a, d) => a + d.options_expected, 0);
  const optionDone = trading.reduce((a, d) => a + d.options_complete, 0);
  const closingMissing = trading.reduce((a, d) => a + (d.closing_missing ?? 0), 0);
  const budget =
    callsToday != null
      ? `${formatNumber(callsToday)} of ${formatNumber(DAILY_LIMIT)} Breeze calls used today`
      : null;
  const pending = c.plan.index_calls + c.plan.option_calls;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex gap-1" role="tablist" aria-label="Instrument">
          {c.underlyings.map((u) => (
            <button
              key={u}
              role="tab"
              aria-selected={u === c.underlying}
              onClick={() => go({ underlying: u })}
              className={cn(
                "rounded-lg px-3 py-1.5 text-sm",
                u === c.underlying
                  ? "bg-accent font-medium text-primary-text"
                  : "text-muted hover:bg-surface-2",
              )}
            >
              {u}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          From
          <input
            type="date"
            className={inputClass}
            value={from}
            max={to}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          To
          <input
            type="date"
            className={inputClass}
            value={to}
            min={from}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <Button variant="secondary" size="sm" onClick={() => go({ refresh: "1" })}>
          Check stored data
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Trading days complete"
          value={`${ok} of ${trading.length}`}
          sub={
            c.first_day
              ? `data from ${c.first_day} to ${c.last_day}`
              : "no index candles stored in this range"
          }
          tone={ok < trading.length ? "warn" : undefined}
        />
        <Tile
          label="Option contract-days stored"
          value={optionDays ? `${Math.round((optionDone / optionDays) * 100)}%` : "–"}
          sub={`${formatNumber(optionDone)} of ${formatNumber(optionDays)} complete`}
          tone={optionDone < optionDays ? "warn" : undefined}
        />
        <Tile
          label="Left to download"
          value={`${formatNumber(c.plan.option_fetches + c.plan.index_days_missing)}`}
          sub={`${formatNumber(c.plan.index_days_missing)} index days · ${formatNumber(c.plan.option_fetches)} option fetches · about ${formatNumber(pending)} calls`}
        />
        <Tile
          label="Closing session (15:30 to 15:39)"
          value={formatNumber(closingMissing)}
          sub="contract-days without candles after 15:30"
          tone={closingMissing ? "warn" : undefined}
        />
      </div>

      {active && <JobProgress job={active} busy={busy} onStop={() => void stop(active.id)} />}

      <Card className="flex flex-col gap-3">
        <CardTitle>Download</CardTitle>
        <p className="text-sm text-muted">
          Fetches only what is missing, from the dates above, into the history backtests use. It runs in the
          background, one download at a time, and continues where it stopped if you stop it or the day&apos;s
          call budget runs out.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-muted">
            Provider
            <select
              className={inputClass}
              value={provider}
              onChange={(e) => setProvider(e.target.value as typeof provider)}
              disabled={Boolean(active)}
            >
              <option value="auto">
                Automatic ({c.default_provider === "none" ? "none set up" : c.default_provider})
              </option>
              <option value="breeze">Breeze</option>
              <option value="kite">Kite</option>
            </select>
          </label>
          <Button onClick={() => void start("fill")} disabled={busy || Boolean(why)}>
            Sync missing data
          </Button>
          <Button variant="secondary" onClick={() => void start("closing")} disabled={busy || Boolean(why)}>
            Fetch closing session
          </Button>
        </div>
        {why && <p className="text-sm text-warning">{why}</p>}
        {error && <p className="text-sm text-loss">{error}</p>}
        <ul className="list-disc pl-5 text-xs text-muted">
          <li>
            <strong>Breeze</strong> has history for contracts that have already expired.{" "}
            {budget ?? "Breeze allows 5,000 calls a day"}, shared with the live price feed (500 are always
            kept back).
          </li>
          <li>
            <strong>Kite</strong> has no data for expired contracts and none for 15:30 to 15:39, so use it
            only for recent days.
          </li>
          <li>
            Market holidays are found by the download itself (a weekday with no candles while its neighbours
            have some) and then stop showing as missing.
          </li>
        </ul>
      </Card>

      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>What is stored, day by day</CardTitle>
          <span className="flex flex-wrap items-center gap-3 text-xs text-muted">
            {(["ok", "partial", "missing", "closed"] as const).map((k) => (
              <span key={k} className="flex items-center gap-1.5">
                <span className={cn("size-3 rounded-[3px]", STATUS_CLASS[k])} aria-hidden /> {STATUS_TEXT[k]}
              </span>
            ))}
          </span>
        </div>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {months.map((m) => (
            <section
              key={m.key}
              aria-label={m.title}
              className="relative flex w-[16rem] shrink-0 flex-col gap-2 rounded-xl border border-border p-3"
            >
              <div className="grid grid-cols-7 gap-1 text-center text-xs text-muted">
                {WEEKDAYS.map((w, i) => (
                  <span key={i}>{w}</span>
                ))}
                {Array.from({ length: m.lead }, (_, i) => (
                  <span key={`l${i}`} />
                ))}
                {m.cells.map(({ date, d }) =>
                  d ? (
                    <button
                      type="button"
                      key={date}
                      aria-label={dayLabel(d)}
                      onMouseEnter={() => setTip(dayLabel(d))}
                      onFocus={() => setTip(dayLabel(d))}
                      onMouseLeave={() => setTip(null)}
                      onBlur={() => setTip(null)}
                      className={cn(
                        "mx-auto size-6 cursor-pointer rounded-md ring-foreground/50 hover:ring-2 focus-visible:ring-2 focus-visible:outline-none",
                        STATUS_CLASS[d.status],
                      )}
                    />
                  ) : (
                    <span key={date} className="mx-auto size-6 rounded-md bg-surface-2" />
                  ),
                )}
              </div>
              <p className="text-center text-sm">{m.title}</p>
            </section>
          ))}
        </div>
        <p role="status" className="min-h-5 text-xs text-muted">
          {tip ?? "Point at a day to see what is stored for it."}
        </p>
      </Card>

      {problems.length > 0 && (
        <Card className="p-0">
          <div className="px-5 pt-4 pb-2">
            <CardTitle>Days with something missing ({problems.length})</CardTitle>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm tabular-nums">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  {[
                    "Day",
                    "Status",
                    "Index candles",
                    "Options complete",
                    "Partial",
                    "Missing",
                    "No closing session",
                  ].map((h) => (
                    <th key={h} className="px-4 py-2 font-medium whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {[...problems]
                  .reverse()
                  .slice(0, 40)
                  .map((d) => (
                    <tr key={d.day}>
                      <td className="px-4 py-2 whitespace-nowrap">{dayTitle.format(at(d.day))}</td>
                      <td className="px-4 py-2">
                        <StatusPill tone={d.status === "missing" ? "danger" : "warning"}>
                          {STATUS_TEXT[d.status]}
                        </StatusPill>
                      </td>
                      <td className="px-4 py-2">{d.index_bars} / 375</td>
                      <td className="px-4 py-2">
                        {d.options_complete} / {d.options_expected}
                      </td>
                      <td className="px-4 py-2">{d.options_partial}</td>
                      <td className="px-4 py-2">{d.options_missing}</td>
                      <td className="px-4 py-2">{d.closing_missing ?? "–"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {problems.length > 40 && (
            <p className="px-5 py-3 text-xs text-muted">The 40 most recent are listed.</p>
          )}
        </Card>
      )}

      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Recent downloads</CardTitle>
        </div>
        {jobs.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">None yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  {["When", "What", "Provider", "Status", "Result"].map((h) => (
                    <th key={h} className="px-4 py-2 font-medium whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="px-4 py-2 whitespace-nowrap">{fmtDateTime(j.created_at)}</td>
                    <td className="px-4 py-2 whitespace-nowrap">
                      {j.underlying} · {j.mode === "closing" ? "closing session" : "missing data"}
                      <span className="block text-xs text-muted">
                        {j.start_date} to {j.end_date}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      {(j.result as { provider?: string } | null)?.provider ?? j.provider}
                    </td>
                    <td className="px-4 py-2">
                      <StatusPill tone={JOB_TONE[j.status]}>{j.status}</StatusPill>
                    </td>
                    <td className="px-4 py-2 text-xs text-muted">{jobSummary(j)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="text-xs text-muted">
        Backups of the database (including this history) and of your settings are separate: see{" "}
        <Link href="https://github.com/rohitkr/algoearning/blob/master/docs/setup.md" className="underline">
          docs/setup.md
        </Link>
        .
      </p>
    </div>
  );
}
