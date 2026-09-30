"use client";

import type { Strategy } from "@algoearning/api-types";
import { Button, StatusPill, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import {
  Archive,
  ArchiveRestore,
  CheckCircle2,
  CircleDashed,
  Copy,
  Pencil,
  Rocket,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";
import { KIND_LABEL, configSummary } from "@/lib/strategy";

import { DeployDialog } from "../runs/deploy-dialog";

const STATUS = {
  draft: { tone: "neutral", label: "Draft" },
  ready: { tone: "success", label: "Ready" },
  archived: { tone: "warning", label: "Archived" },
} as const;
const updated = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

export function StrategyRow({ strategy: s }: { strategy: Strategy }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; upgrade?: boolean } | null>(null);
  const [deploying, setDeploying] = useState(false);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      const api = e instanceof ApiRequestError ? e : null;
      setError({ text: api?.message ?? (e as Error).message, upgrade: api?.body?.code === "plan_limit" });
    } finally {
      setBusy(false);
    }
  }
  const setStatus = (status: Strategy["status"]) =>
    run(() => apiRequest("PATCH", `/v1/strategies/${s.id}`, { status }, getToken));
  const duplicate = () => run(() => apiRequest("POST", `/v1/strategies/${s.id}/duplicate`, {}, getToken));
  const remove = () => {
    if (!confirm(`Delete "${s.name}"? This cannot be undone.`)) return;
    return run(() => apiRequest("DELETE", `/v1/strategies/${s.id}`, undefined, getToken));
  };
  const st = STATUS[s.status];

  return (
    <li className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-48 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/builder/${s.id}`} className="font-semibold hover:underline">
              {s.name}
            </Link>
            <StatusPill tone={st.tone}>{st.label}</StatusPill>
            <StatusPill tone="info">{KIND_LABEL[s.kind]}</StatusPill>
          </div>
          <p className="mt-1 text-sm text-muted">{configSummary(s.config)}</p>
          <p className="text-xs text-muted">
            Version {s.version} · updated {updated.format(new Date(s.updated_at))}
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Button asChild size="sm" variant="secondary">
            <Link href={`/builder/${s.id}`}>
              <Pencil className="size-4" aria-hidden /> Edit
            </Link>
          </Button>
          {s.status === "draft" && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus("ready")}>
              <CheckCircle2 className="size-4" aria-hidden /> Mark ready
            </Button>
          )}
          {s.status === "ready" && (
            <Button size="sm" disabled={busy} onClick={() => setDeploying(true)}>
              <Rocket className="size-4" aria-hidden /> Deploy
            </Button>
          )}
          {s.status === "ready" && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setStatus("draft")}>
              <CircleDashed className="size-4" aria-hidden /> Back to draft
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            disabled={busy}
            onClick={duplicate}
            aria-label={`Duplicate ${s.name}`}
          >
            <Copy className="size-4" aria-hidden />
          </Button>
          {s.status === "archived" ? (
            <Button
              size="icon"
              variant="ghost"
              disabled={busy}
              onClick={() => setStatus("draft")}
              aria-label={`Restore ${s.name}`}
            >
              <ArchiveRestore className="size-4" aria-hidden />
            </Button>
          ) : (
            <Button
              size="icon"
              variant="ghost"
              disabled={busy}
              onClick={() => setStatus("archived")}
              aria-label={`Archive ${s.name}`}
            >
              <Archive className="size-4" aria-hidden />
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            disabled={busy}
            onClick={remove}
            aria-label={`Delete ${s.name}`}
          >
            <Trash2 className="size-4" aria-hidden />
          </Button>
        </div>
      </div>
      {deploying && <DeployDialog strategy={s} open onClose={() => setDeploying(false)} />}
      {error && (
        <p role="status" className={cn("mt-2 text-sm text-loss")}>
          {error.text}{" "}
          {error.upgrade && (
            <Link href="/subscription" className="text-primary-text underline">
              See plans
            </Link>
          )}
        </p>
      )}
    </li>
  );
}
