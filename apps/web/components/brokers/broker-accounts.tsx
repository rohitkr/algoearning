"use client";

import type { BrokerAccount, BrokerInfo } from "@algoearning/api-types";
import { Button, Card, StatusPill, Switch, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { LogIn, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRequestError, apiRequest } from "@/lib/client-api";

import { AddBrokerDialog } from "./add-broker-dialog";

const BRAND: Record<string, string> = {
  zerodha: "bg-[#387ed1]",
  upstox: "bg-[#5a2e8c]",
  angelone: "bg-[#f4a52a]",
};
const time = new Intl.DateTimeFormat("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit" });

function StatusLine({ a }: { a: BrokerAccount }) {
  if (a.status === "connected")
    return (
      <StatusPill tone="success">
        Connected{a.session_expires_at ? ` · until ${time.format(new Date(a.session_expires_at))}` : ""}
      </StatusPill>
    );
  if (a.status === "expired") return <StatusPill tone="warning">Session expired · log in again</StatusPill>;
  if (a.status === "error") return <StatusPill tone="danger">Error</StatusPill>;
  return <StatusPill tone="danger">Not connected</StatusPill>;
}

export function BrokerAccounts({ accounts, catalog }: { accounts: BrokerAccount[]; catalog: BrokerInfo[] }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ id: string; tone: "success" | "danger"; text: string } | null>(null);
  const [adding, setAdding] = useState(false);

  async function run(id: string, fn: () => Promise<void>) {
    setBusy(id);
    setNote(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setNote({ id, tone: "danger", text: e instanceof ApiRequestError ? e.message : (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const login = (a: BrokerAccount) =>
    run(a.id, async () => {
      const { login_url } = await apiRequest<{ login_url: string }>(
        "POST",
        `/v1/broker-accounts/${a.id}/login`,
        undefined,
        getToken,
      );
      window.location.assign(login_url);
    });
  const patch = (a: BrokerAccount, body: Record<string, unknown>) =>
    run(a.id, () => apiRequest("PATCH", `/v1/broker-accounts/${a.id}`, body, getToken));
  const test = (a: BrokerAccount) =>
    run(a.id, async () => {
      const r = await apiRequest<{ ok: boolean; client_id?: string; name?: string; message?: string }>(
        "POST",
        `/v1/broker-accounts/${a.id}/test`,
        undefined,
        getToken,
      );
      setNote({
        id: a.id,
        tone: r.ok ? "success" : "danger",
        text: r.ok ? `Connection OK: ${r.name ?? ""} (${r.client_id})` : (r.message ?? "Not connected"),
      });
    });
  const remove = (a: BrokerAccount) => {
    if (!confirm(`Remove ${a.broker_name} ${a.client_id}? Its stored API keys are deleted.`)) return;
    return run(a.id, () => apiRequest("DELETE", `/v1/broker-accounts/${a.id}`, undefined, getToken));
  };

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Broker</h1>
          <p className="text-sm text-muted">
            Manage your connected brokers. Log in once each trading day (after 6:00 AM).
          </p>
        </div>
        <Button onClick={() => setAdding(true)}>
          <Plus className="size-4" aria-hidden /> Add broker
        </Button>
      </div>

      {accounts.length === 0 && (
        <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
          No brokers yet. Add your Zerodha account to trade from AlgoEarning.
        </p>
      )}

      {accounts.map((a) => (
        <div key={a.id} className="rounded-xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center gap-4">
            <span
              className={cn(
                "grid size-11 shrink-0 place-items-center rounded-full font-semibold text-white",
                BRAND[a.broker] ?? "bg-primary",
              )}
              aria-hidden
            >
              {a.broker_name.slice(0, 1)}
            </span>
            <div className="min-w-44 flex-1">
              <p className="font-semibold">
                {a.broker_name} {a.label && <span className="font-normal text-muted">· {a.label}</span>}
              </p>
              <p className="text-sm text-primary-text">{a.client_id}</p>
              <p className="text-xs text-muted">
                Static IP: {a.static_ip ?? "Not assigned"} · API key {a.api_key_masked}
              </p>
              <div className="mt-1.5">
                <StatusLine a={a} />
              </div>
            </div>
            <div className="text-center">
              <p className="text-xs text-muted">Strategy performance</p>
              <p className="font-semibold tabular-nums">–</p>
            </div>
            <label className="flex flex-col items-center gap-1 text-xs text-muted">
              Terminal
              <Switch
                checked={a.terminal_enabled}
                disabled={busy === a.id}
                onCheckedChange={(on) =>
                  on && a.status !== "connected" ? login(a) : patch(a, { terminal_enabled: on })
                }
                aria-label={`Terminal for ${a.client_id}`}
              />
            </label>
            <label className="flex flex-col items-center gap-1 text-xs text-muted">
              Trading engine
              <Switch
                checked={a.engine_enabled}
                disabled={busy === a.id || a.status !== "connected"}
                onCheckedChange={(on) => patch(a, { engine_enabled: on })}
                aria-label={`Trading engine for ${a.client_id}`}
              />
            </label>
            <div className="flex gap-1">
              {a.status !== "connected" ? (
                <Button size="sm" onClick={() => login(a)} disabled={busy === a.id}>
                  <LogIn className="size-4" aria-hidden /> Login
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => test(a)} disabled={busy === a.id}>
                  <RefreshCw className="size-4" aria-hidden /> Test
                </Button>
              )}
              <Button
                size="icon"
                variant="ghost"
                onClick={() => remove(a)}
                disabled={busy === a.id}
                aria-label={`Remove ${a.client_id}`}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
          {note?.id === a.id && (
            <p
              role="status"
              className={cn("mt-3 text-sm", note.tone === "success" ? "text-profit" : "text-loss")}
            >
              {note.text}
            </p>
          )}
        </div>
      ))}

      <AddBrokerDialog open={adding} onClose={() => setAdding(false)} catalog={catalog} />
    </Card>
  );
}
