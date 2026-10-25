"use client";

import type { PreviewSet, StrategyConfig } from "@algoearning/api-types";
import { formatNumber } from "@algoearning/shared";
import { Button, Card, CardTitle, Pnl, StatusPill, cn, useConfirm } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Play, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";
import { analyse, bs, impliedVol, type PayoffLeg, yearsTo } from "@/lib/payoff";

const money = (v: number) => `₹${formatNumber(Math.round(v))}`;
const pct = (v: number, base: number) => `${v >= 0 ? "+" : ""}${((v / base) * 100).toFixed(1)}%`;
const DIR_LABEL: Record<string, string> = { always: "", up: "On an up signal", down: "On a down signal" };

/** "What it would trade now": the contracts the strategy's legs pick at this moment (live index and option prices,
 * the engine's own choice) with their payoff at expiry and today. Telegram tips traded as given have none. */
export function LegsPreview({
  config,
  multiplier = 1,
  runId,
  live = false,
  brokerAccountId = null,
}: {
  config: StrategyConfig;
  multiplier?: number;
  runId?: string; // on a run's page: "Enter now" trades exactly the legs shown
  live?: boolean;
  brokerAccountId?: string | null; // whose margin and funds to check (the run's account)
}) {
  const { getToken } = useAuth();
  const token = useRef(getToken);
  const router = useRouter();
  const confirm = useConfirm();
  const [sets, setSets] = useState<PreviewSet[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState(0);
  const [strikes, setStrikes] = useState<Record<string, Record<string, number>>>({}); // moved by hand
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const body = useMemo(
    () => JSON.stringify({ config, multiplier, strikes, broker_account_id: brokerAccountId }),
    [config, multiplier, strikes, brokerAccountId],
  );

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await apiRequest<PreviewSet[]>(
        "POST",
        "/v1/strategies/preview-legs",
        JSON.parse(body),
        token.current,
      );
      setSets(r);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [body]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250); // after quick clicks settle
    return () => clearTimeout(t);
  }, [load]);

  if (sets && sets.length === 0) return null; // nothing of its own to preview (a tip traded as given)
  const set = sets?.[Math.min(pick, (sets?.length ?? 1) - 1)];
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle>What it would trade now</CardTitle>
          <p className="text-sm text-muted">
            The strikes the strategy picks at this moment from live prices, and the payoff of that trade. It
            changes as the market moves; the real entry is chosen at the entry time.
          </p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          title="Pick the strikes again from the latest prices (undoes strikes moved by hand)"
          onClick={() => {
            if (Object.keys(strikes).length)
              setStrikes({}); // reloads through the body change
            else void load();
          }}
          disabled={busy}
        >
          <RefreshCw className={cn("size-3.5", busy && "animate-spin")} aria-hidden /> Refresh
        </Button>
      </div>
      {sets && sets.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {sets.map((s, i) => (
            <button
              key={`${s.plan}-${s.direction}-${i}`}
              type="button"
              onClick={() => setPick(i)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-semibold",
                i === pick
                  ? "bg-primary text-primary-foreground"
                  : "bg-surface-2 text-muted hover:text-foreground",
              )}
            >
              {[s.plan, DIR_LABEL[s.direction]].filter(Boolean).join(" · ") || s.underlying}
            </button>
          ))}
        </div>
      )}
      {error && <p className="text-sm text-loss">{error}</p>}
      {!set ? (
        <p className="text-sm text-muted">Reading live prices…</p>
      ) : set.reason ? (
        <p className="text-sm text-muted">Cannot pick the strikes right now: {set.reason}.</p>
      ) : (
        <>
          <p className="text-sm">
            <span className="font-medium">{set.entry_note}</span>
          </p>
          <PreviewBody
            set={set}
            move={(leg, by) => {
              const l = set.legs.find((x) => x.leg === leg);
              if (!l) return;
              const strike = l.strike + by * set.strike_step;
              // at once on screen: the new strike priced from the leg's own implied volatility, until the live price
              // arrives a moment later
              setSets((cur) => (cur ? cur.map((x) => (x === set ? reprice(x, leg, strike) : x)) : cur));
              setStrikes((cur) => ({
                ...cur,
                [set.plan_id]: { ...(cur[set.plan_id] ?? {}), [leg]: strike },
              }));
            }}
          />
          {runId && set.legs.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              <Button
                variant={live ? "danger" : "primary"}
                disabled={busy}
                onClick={async () => {
                  const short =
                    set.margin_needed != null &&
                    set.funds_available != null &&
                    set.margin_needed * 1.1 > set.funds_available;
                  if (short) {
                    setNote({ ok: false, text: set.margin_note ?? "Not enough funds for these legs." });
                    return;
                  }
                  const age = set.prices_at ? (Date.now() - Date.parse(set.prices_at)) / 1000 : Infinity;
                  const stale = age > 60;
                  const moved = set.legs.some((l) => l.strike !== l.algo_strike);
                  const ok = await confirm({
                    title: "Enter this trade now?",
                    message:
                      set.legs.map((l) => `${l.action} ${l.lots} lot ${l.tradingsymbol_hint}`).join(", ") +
                      "." +
                      (moved ? " Some strikes were moved by hand." : "") +
                      (stale
                        ? ` WARNING: these prices are ${Number.isFinite(age) ? `${Math.round(age / 60)} min` : "of unknown age"} old (market closed or no live feed): press Refresh first, or the orders may fill far from them.`
                        : "") +
                      (live ? " This is a live run: real orders go to your broker." : ""),
                    confirmLabel: "Enter now",
                    tone: live || stale ? "danger" : undefined,
                  });
                  if (!ok) return;
                  try {
                    await apiRequest(
                      "POST",
                      `/v1/runs/${runId}/enter-now`,
                      {
                        contracts: { [set.plan_id]: Object.fromEntries(set.legs.map((l) => [l.leg, l.key])) },
                      },
                      token.current,
                    );
                    setNote({ ok: true, text: "Asked the engine to enter these legs now." });
                    router.refresh();
                  } catch (e) {
                    setNote({
                      ok: false,
                      text: e instanceof ApiRequestError ? e.message : (e as Error).message,
                    });
                  }
                }}
              >
                <Play className="size-4" aria-hidden /> Enter now with these legs
              </Button>
              {note && (
                <span className={cn("text-sm", note.ok ? "text-muted" : "text-loss")}>{note.text}</span>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/** The order the owner reads an iron fly in: buy PE, sell PE, sell CE, buy CE (strikes rising left to right). The
 * orders themselves go hedge first, one side at a time (buy PE, sell PE, buy CE, sell CE). */
export function displayOrder<T extends { action: string; option_type: string; strike: number }>(
  legs: T[],
): T[] {
  const rank = (l: T) =>
    l.option_type === "PE"
      ? l.action === "BUY"
        ? 0
        : 1
      : l.option_type === "CE"
        ? l.action === "SELL"
          ? 2
          : 3
        : 4;
  return [...legs].sort((a, b) => rank(a) - rank(b) || a.strike - b.strike);
}

/** The set with one leg moved to `strike`, its price estimated (Black-Scholes at the leg's implied volatility). */
function reprice(set: PreviewSet, leg: string, strike: number): PreviewSet {
  const spot = set.spot ?? 0;
  return {
    ...set,
    legs: set.legs.map((l) => {
      if (l.leg !== leg) return l;
      const right = l.option_type as "CE" | "PE";
      const t = yearsTo(l.expiry, new Date());
      const price =
        l.price != null && spot
          ? Math.round(bs(right, spot, strike, t, impliedVol(right, spot, l.strike, t, l.price)) * 20) / 20
          : null;
      return { ...l, strike, price, key: l.key.replace(/:\d+:(CE|PE)$/, `:${strike}:$1`) };
    }),
  };
}

function PreviewBody({ set, move }: { set: PreviewSet; move: (leg: string, by: number) => void }) {
  const spot = set.spot ?? 0;
  const priced = set.legs.every((l) => l.price != null);
  const a = useMemo(() => {
    const legs: PayoffLeg[] = set.legs.map((l) => ({
      action: l.action as "BUY" | "SELL",
      right: l.option_type as "CE" | "PE",
      strike: l.strike,
      qty: l.qty,
      price: l.price ?? 0,
      expiry: l.expiry,
    }));
    return priced && spot ? analyse(legs, spot) : null;
  }, [set.legs, priced, spot]);
  const capital = Math.max(1, Math.abs(a?.maxLoss ?? 0) || Math.abs(a?.premium ?? 1));
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="flex flex-col gap-3">
        <p className="text-sm">
          <span className="font-semibold">{set.underlying}</span>{" "}
          <span className="text-lg font-semibold tabular-nums">{formatNumber(spot)}</span>
          {set.prices_at && (
            <span className="ml-2 text-xs text-muted">
              prices as of{" "}
              {new Date(set.prices_at).toLocaleTimeString("en-IN", {
                timeZone: "Asia/Kolkata",
                hour12: false,
              })}
            </span>
          )}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm tabular-nums">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                {["B/S", "Expiry", "Strike", "Type", "Lots", "Price"].map((h) => (
                  <th key={h} className="px-2 py-1.5 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {displayOrder(set.legs).map((l) => (
                <tr key={l.leg}>
                  <td className="px-2 py-2">
                    <StatusPill tone={l.action === "BUY" ? "success" : "danger"}>
                      {l.action === "BUY" ? "B" : "S"}
                    </StatusPill>
                  </td>
                  <td className="px-2 py-2">{l.expiry}</td>
                  <td className="px-2 py-2">
                    <span className="inline-flex items-center gap-1">
                      <button
                        type="button"
                        aria-label={`Lower the ${l.leg} strike`}
                        className="rounded border border-border px-1.5 text-muted hover:text-foreground"
                        onClick={() => move(l.leg, -1)}
                      >
                        −
                      </button>
                      <span
                        className={cn(
                          "min-w-14 text-center font-semibold",
                          l.strike !== l.algo_strike && "text-warning",
                        )}
                      >
                        {l.strike}
                      </span>
                      <button
                        type="button"
                        aria-label={`Raise the ${l.leg} strike`}
                        className="rounded border border-border px-1.5 text-muted hover:text-foreground"
                        onClick={() => move(l.leg, 1)}
                      >
                        +
                      </button>
                    </span>
                  </td>
                  <td className="px-2 py-2">{l.option_type}</td>
                  <td className="px-2 py-2">
                    {l.lots} <span className="text-xs text-muted">({l.qty})</span>
                  </td>
                  <td className="px-2 py-2">{l.price == null ? "–" : formatNumber(l.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {a && (
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
            <Stat label="Max profit" value={a.maxProfit == null ? "Unlimited" : money(a.maxProfit)} good />
            <Stat label="Max loss" value={a.maxLoss == null ? "Unlimited" : money(a.maxLoss)} bad />
            <Stat
              label="Reward / risk"
              value={a.maxProfit != null && a.maxLoss ? (a.maxProfit / Math.abs(a.maxLoss)).toFixed(2) : "–"}
            />
            <Stat
              label="Breakeven"
              value={a.breakevens.map((b) => `${b} (${pct(b - spot, spot)})`).join(", ") || "–"}
              wide
            />
            <Stat
              label={a.premium >= 0 ? "Premium received" : "Premium paid"}
              value={money(Math.abs(a.premium))}
            />
            {set.margin_needed != null && (
              <Stat
                label="Margin needed"
                value={money(set.margin_needed)}
                bad={set.funds_available != null && set.margin_needed * 1.1 > set.funds_available}
              />
            )}
            {set.funds_available != null && (
              <Stat label="Funds available" value={money(set.funds_available)} />
            )}
          </div>
        )}
        {!priced && (
          <p className="text-xs text-muted">Some legs have no live price yet: refresh in a moment.</p>
        )}
        {set.margin_note && (
          <p
            className={cn(
              "text-sm",
              set.margin_needed != null && set.funds_available != null
                ? "font-medium text-loss"
                : "text-muted",
            )}
          >
            {set.margin_note}
          </p>
        )}
      </div>
      {a && <PayoffChart a={a} spot={spot} capital={capital} />}
    </div>
  );
}

function Stat({
  label,
  value,
  good,
  bad,
  wide,
}: {
  label: string;
  value: string;
  good?: boolean;
  bad?: boolean;
  wide?: boolean;
}) {
  return (
    <div className={cn("rounded-lg border border-border px-3 py-2", wide && "col-span-2")}>
      <p className="text-xs text-muted">{label}</p>
      <p className={cn("font-semibold tabular-nums", good && "text-profit", bad && "text-loss")}>{value}</p>
    </div>
  );
}

/** Payoff at expiry (green above zero, red below) and today (blue), over spot ± 6%, with ±1/2 SD marks; hover for the
 * P&L at any index level. */
function PayoffChart({ a, spot, capital }: { a: ReturnType<typeof analyse>; spot: number; capital: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560;
  const H = 300;
  const pad = { l: 56, r: 12, t: 24, b: 28 };
  const xs = a.points.map((p) => p.s);
  const ys = a.points.flatMap((p) => [p.expiry, p.today]);
  const x0 = xs[0]!;
  const x1 = xs[xs.length - 1]!;
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(0, ...ys);
  const yr = yMax - yMin || 1;
  const X = (s: number) => pad.l + ((s - x0) / (x1 - x0)) * (W - pad.l - pad.r);
  const Y = (v: number) => pad.t + ((yMax - v) / yr) * (H - pad.t - pad.b);
  const line = (k: "expiry" | "today") =>
    a.points.map((p, i) => `${i ? "L" : "M"}${X(p.s).toFixed(1)},${Y(p[k]).toFixed(1)}`).join("");
  const zero = Y(0);
  const area = `${line("expiry")}L${X(x1)},${zero}L${X(x0)},${zero}Z`;
  const ticks = Array.from({ length: 5 }, (_, i) => yMin + (yr * i) / 4);
  const sdMarks = [-2, -1, 1, 2].map((k) => spot + k * a.sd).filter((s) => s > x0 && s < x1);
  const h = hover == null ? null : a.points[hover]!;
  return (
    <div className="relative">
      <p className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Payoff</p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full touch-none select-none"
        role="img"
        aria-label="Payoff at expiry and today"
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const sx = ((e.clientX - r.left) / r.width) * W;
          const s = x0 + ((sx - pad.l) / (W - pad.l - pad.r)) * (x1 - x0);
          const i = Math.round(((s - x0) / (x1 - x0)) * (a.points.length - 1));
          setHover(Math.max(0, Math.min(a.points.length - 1, i)));
        }}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <clipPath id="above">
            <rect x={0} y={0} width={W} height={zero} />
          </clipPath>
          <clipPath id="below">
            <rect x={0} y={zero} width={W} height={H - zero} />
          </clipPath>
        </defs>
        {ticks.map((v) => (
          <g key={v}>
            <line
              x1={pad.l}
              x2={W - pad.r}
              y1={Y(v)}
              y2={Y(v)}
              stroke="var(--color-border)"
              strokeWidth={0.5}
            />
            <text x={pad.l - 6} y={Y(v) + 3} textAnchor="end" fontSize={10} fill="var(--color-muted)">
              {Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}K` : v.toFixed(0)}
            </text>
          </g>
        ))}
        <path d={area} fill="var(--color-profit)" opacity={0.12} clipPath="url(#above)" />
        <path d={area} fill="var(--color-loss)" opacity={0.12} clipPath="url(#below)" />
        <path
          d={line("expiry")}
          fill="none"
          stroke="var(--color-profit)"
          strokeWidth={2}
          clipPath="url(#above)"
        />
        <path
          d={line("expiry")}
          fill="none"
          stroke="var(--color-loss)"
          strokeWidth={2}
          clipPath="url(#below)"
        />
        <path d={line("today")} fill="none" stroke="var(--color-primary)" strokeWidth={1.5} />
        <line
          x1={pad.l}
          x2={W - pad.r}
          y1={zero}
          y2={zero}
          stroke="var(--color-muted)"
          strokeDasharray="3 3"
          strokeWidth={0.8}
        />
        {sdMarks.map((s, i) => (
          <g key={s}>
            <line
              x1={X(s)}
              x2={X(s)}
              y1={pad.t}
              y2={H - pad.b}
              stroke="var(--color-muted)"
              strokeDasharray="2 4"
              strokeWidth={0.6}
            />
            <text x={X(s)} y={pad.t - 6} textAnchor="middle" fontSize={9} fill="var(--color-muted)">
              {["-2SD", "-1SD", "+1SD", "+2SD"][[-2, -1, 1, 2].indexOf(Math.round((s - spot) / a.sd))] ?? ""}
              {i < 0 ? "" : ""}
            </text>
          </g>
        ))}
        <line
          x1={X(spot)}
          x2={X(spot)}
          y1={pad.t}
          y2={H - pad.b}
          stroke="#f59e0b"
          strokeDasharray="4 3"
          strokeWidth={1}
        />
        <text x={X(spot)} y={H - pad.b + 22} textAnchor="middle" fontSize={10} fill="#f59e0b">
          {formatNumber(Math.round(spot))}
        </text>
        {[x0, (x0 + spot) / 2, (spot + x1) / 2, x1].map((s) => (
          <text
            key={s}
            x={X(s)}
            y={H - pad.b + 12}
            textAnchor="middle"
            fontSize={10}
            fill="var(--color-muted)"
          >
            {formatNumber(Math.round(s))}
          </text>
        ))}
        {h && (
          <g>
            <line
              x1={X(h.s)}
              x2={X(h.s)}
              y1={pad.t}
              y2={H - pad.b}
              stroke="var(--color-foreground)"
              strokeWidth={0.6}
            />
            <circle
              cx={X(h.s)}
              cy={Y(h.expiry)}
              r={3}
              fill={h.expiry >= 0 ? "var(--color-profit)" : "var(--color-loss)"}
            />
            <circle cx={X(h.s)} cy={Y(h.today)} r={3} fill="var(--color-primary)" />
          </g>
        )}
      </svg>
      <div className="mt-1 flex gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-profit" /> On expiry
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-primary" /> Today
        </span>
      </div>
      {h && (
        <div className="pointer-events-none absolute top-6 right-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg">
          <p className="text-muted">When the index is at</p>
          <p className="text-sm font-semibold tabular-nums">
            {formatNumber(Math.round(h.s))}{" "}
            <span className={h.s >= spot ? "text-profit" : "text-loss"}>
              {pct(h.s - spot, spot)} ({h.s >= spot ? "+" : ""}
              {Math.round(h.s - spot)})
            </span>
          </p>
          <p className="mt-1 flex justify-between gap-6">
            Today <Pnl value={Math.round(h.today)} />
          </p>
          <p className="flex justify-between gap-6">
            On expiry <Pnl value={Math.round(h.expiry)} />
          </p>
          <p className="mt-1 text-muted">{pct(h.expiry, capital)} of the risk</p>
        </div>
      )}
    </div>
  );
}
