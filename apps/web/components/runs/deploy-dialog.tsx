"use client";

import type { LiveStatus, Preflight, Run, Strategy } from "@algoearning/api-types";
import { Button, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

type Mode = "paper" | "dry" | "live";

/** Deploy a ready strategy: paper, live as a dry run (orders logged, none sent), or live with real orders. Real
 * orders need the plan, an admin's unlock, a broker logged in today with its Trading Engine on, and the strategy's
 * name typed to confirm. */
export function DeployDialog({
  strategy,
  open,
  onClose,
}: {
  strategy: Strategy;
  open: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { getToken } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("paper");
  const [multiplier, setMultiplier] = useState(1);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [broker, setBroker] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [pre, setPre] = useState<Preflight | null>(null);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);

  // load once per opening (getToken's identity is not guaranteed to be stable)
  const tokenRef = useRef(getToken);
  useEffect(() => {
    tokenRef.current = getToken;
  }, [getToken]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    apiRequest<LiveStatus>("GET", "/v1/me/live", undefined, () => tokenRef.current())
      .then((s) => {
        if (!alive) return;
        setLive(s);
        setBroker(s.brokers.find((b) => b.connected && b.engine_enabled)?.id ?? "");
      })
      .catch(() => alive && setLive(null));
    return () => {
      alive = false;
    };
  }, [open]);

  async function preflight() {
    setPre(null);
    try {
      setPre(
        await apiRequest<Preflight>(
          "POST",
          `/v1/me/live/preflight?broker_account_id=${broker}`,
          undefined,
          getToken,
        ),
      );
    } catch (e) {
      setError({ text: (e as Error).message });
    }
  }

  const ready = mode !== "live" || (confirmText.trim() === strategy.name.trim() && !!broker);

  async function deploy() {
    setBusy(true);
    setError(null);
    const body =
      mode === "paper"
        ? { mode: "paper", multiplier }
        : mode === "dry"
          ? { mode: "live", dry_run: true, multiplier }
          : { mode: "live", multiplier, broker_account_id: broker, confirm: confirmText };
    try {
      const run = await apiRequest<Run>("POST", `/v1/strategies/${strategy.id}/deploy`, body, getToken);
      onClose();
      router.push(`/runs/${run.id}`);
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      setError({
        text: api?.message ?? (e as Error).message,
        upgrade: api?.body?.code === "plan_limit" || api?.body?.code === "plan_feature",
      });
    } finally {
      setBusy(false);
    }
  }

  const option = (value: Mode, label: string, hint: string, disabled = false) => (
    <label className={cn("flex items-start gap-2 text-sm", disabled && "text-muted")}>
      <input
        type="radio"
        name="mode"
        className="mt-1"
        checked={mode === value}
        disabled={disabled}
        onChange={() => setMode(value)}
      />
      <span>
        {label}
        <span className="block text-xs text-muted">{hint}</span>
      </span>
    </label>
  );

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={`Deploy ${strategy.name}`}
      className="m-auto w-[min(30rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
    >
      <div className="flex flex-col gap-4 p-6">
        <div>
          <h2 className="text-lg font-semibold">Deploy {strategy.name}</h2>
          <p className="mt-1 text-sm text-muted">
            It runs every trading day on live prices until you stop it.
          </p>
        </div>
        <fieldset className="flex flex-col gap-2.5">
          <legend className="mb-1 text-xs font-medium text-muted">Mode</legend>
          {option("paper", "Paper trading", "Simulated fills. No orders anywhere.")}
          {option(
            "dry",
            "Live, dry run",
            "Every order is worked out and logged, none is sent.",
            !live?.can_dry_run,
          )}
          {option("live", "Live, real orders", "Orders go to your broker account.", !live?.can_go_live)}
        </fieldset>
        {live && !live.can_go_live && live.reasons.length > 0 && (
          <ul className="list-disc pl-5 text-xs text-muted">
            {live.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {mode === "live" && live && (
          <div className="flex flex-col gap-3 rounded-lg border border-loss/40 bg-loss/10 p-3">
            <p className="flex items-start gap-2 text-sm font-medium text-loss">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              Real money: orders are placed on your broker account.
            </p>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted">
              Broker account
              <select className={inputClass} value={broker} onChange={(e) => setBroker(e.target.value)}>
                {live.brokers
                  .filter((b) => b.connected && b.engine_enabled)
                  .map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.client_id}
                      {b.label ? ` · ${b.label}` : ""}
                    </option>
                  ))}
              </select>
            </label>
            <div className="flex flex-col gap-1.5">
              <Button size="sm" variant="secondary" className="w-fit" disabled={!broker} onClick={preflight}>
                Check everything is ready
              </Button>
              {pre && (
                <ul className="text-xs" aria-label="Pre-flight checks">
                  {pre.checks.map((c) => (
                    <li key={c.name} className={c.ok ? "text-profit" : "text-loss"}>
                      {c.ok ? "✓" : "✗"} {c.name}: {c.detail}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <label className="flex flex-col gap-1 text-xs font-medium text-muted">
              Type <span className="font-semibold text-foreground">{strategy.name}</span> to confirm
              <input
                className={inputClass}
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                autoComplete="off"
              />
            </label>
          </div>
        )}
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          Multiplier (every leg&apos;s lots × this)
          <input
            type="number"
            min={1}
            max={100}
            className={`${inputClass} w-28`}
            value={multiplier}
            onChange={(e) =>
              setMultiplier(Math.max(1, Math.min(100, Math.trunc(e.target.valueAsNumber || 1))))
            }
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-loss">
            {error.text}{" "}
            {error.upgrade && (
              <Link href="/subscription" className="text-primary-text underline">
                See plans
              </Link>
            )}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={deploy} disabled={busy || !ready} variant={mode === "live" ? "danger" : "primary"}>
            {busy
              ? "Deploying…"
              : mode === "paper"
                ? "Deploy on paper"
                : mode === "dry"
                  ? "Start dry run"
                  : "Deploy with real orders"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
