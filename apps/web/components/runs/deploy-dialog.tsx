"use client";

import type { Run, Strategy } from "@algoearning/api-types";
import { Button } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { inputClass } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

/** Deploy a ready strategy. Paper only for now: live orders ship after paper trading has been watched. */
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
  const [multiplier, setMultiplier] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal?.();
    if (!open && d.open) d.close?.();
  }, [open]);

  async function deploy() {
    setBusy(true);
    setError(null);
    try {
      const run = await apiRequest<Run>(
        "POST",
        `/v1/strategies/${strategy.id}/deploy`,
        { mode: "paper", multiplier },
        getToken,
      );
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

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={`Deploy ${strategy.name}`}
      className="m-auto w-[min(28rem,calc(100%-2rem))] rounded-2xl border border-border bg-surface p-0 text-foreground shadow-card backdrop:bg-black/50"
    >
      <div className="flex flex-col gap-4 p-6">
        <div>
          <h2 className="text-lg font-semibold">Deploy {strategy.name}</h2>
          <p className="mt-1 text-sm text-muted">
            It runs every trading day on live prices until you stop it. Paper trading places no real orders.
          </p>
        </div>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-xs font-medium text-muted">Mode</legend>
          <label className="flex items-center gap-2 text-sm">
            <input type="radio" name="mode" checked readOnly /> Paper trading
          </label>
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="radio" name="mode" disabled /> Live (coming soon)
          </label>
        </fieldset>
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
          <Button onClick={deploy} disabled={busy}>
            {busy ? "Deploying…" : "Deploy on paper"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
