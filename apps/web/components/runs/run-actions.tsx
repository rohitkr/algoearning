"use client";

import type { RiskSettings } from "@algoearning/api-types";
import { Button, Card, CardTitle, Switch, cn } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { OctagonX, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { NumberField } from "@/components/builder/fields";
import { ApiRequestError, apiRequest } from "@/lib/client-api";

function useCall() {
  const { getToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  async function call(method: string, path: string, body?: unknown, done?: string) {
    setBusy(true);
    setNote(null);
    try {
      await apiRequest(method, path, body, getToken);
      router.refresh();
      if (done) setNote({ ok: true, text: done });
    } catch (e) {
      setNote({ ok: false, text: e instanceof ApiRequestError ? e.message : (e as Error).message });
    } finally {
      setBusy(false);
    }
  }
  return { call, busy, note };
}

export function StopRunButton({ runId, name }: { runId: string; name: string }) {
  const { call, busy, note } = useCall();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="danger"
        disabled={busy}
        onClick={() =>
          confirm(`Stop ${name}? Its open positions are squared off now.`) &&
          call("POST", `/v1/runs/${runId}/stop`)
        }
      >
        <Square className="size-3.5" aria-hidden /> Stop
      </Button>
      {note && !note.ok && <span className="text-xs text-loss">{note.text}</span>}
    </span>
  );
}

export function StopAllButton({ count }: { count: number }) {
  const { call, busy } = useCall();
  if (count === 0) return null;
  return (
    <Button
      variant="danger"
      disabled={busy}
      onClick={() =>
        confirm(`Stop all ${count} running strategies and square off every open position?`) &&
        call("POST", "/v1/runs/stop-all")
      }
    >
      <OctagonX className="size-4" aria-hidden /> Stop all
    </Button>
  );
}

/** The user's own limits across all their runs; the engine checks them before every entry. */
export function RiskSettingsCard({ initial }: { initial: RiskSettings }) {
  const { call, busy, note } = useCall();
  const [v, setV] = useState<RiskSettings>(initial);
  const set = <K extends keyof RiskSettings>(k: K, x: RiskSettings[K]) => setV((o) => ({ ...o, [k]: x }));
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>Your risk limits</CardTitle>
          <p className="mt-1 text-sm text-muted">
            Across all your strategies, every day. Leave a box empty for no limit.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium">
          Kill switch
          <Switch
            checked={!!v.kill_switch}
            aria-label="Kill switch: square off everything and stop new entries"
            onCheckedChange={(on) => {
              if (
                on &&
                !confirm(
                  "Turn on the kill switch? Every open position is squared off and no new entries are made.",
                )
              )
                return;
              set("kill_switch", on);
              void call(
                "PUT",
                "/v1/me/risk",
                { ...v, kill_switch: on },
                on ? "Kill switch on." : "Kill switch off.",
              );
            }}
          />
        </label>
      </div>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void call("PUT", "/v1/me/risk", v, "Limits saved.");
        }}
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <NumberField
            label="Max loss per day"
            optional
            suffix="₹"
            min={0}
            value={v.max_daily_loss}
            onChange={(x) => set("max_daily_loss", x)}
          />
          <NumberField
            label="Profit target per day"
            optional
            suffix="₹"
            min={0}
            value={v.max_daily_profit}
            onChange={(x) => set("max_daily_profit", x)}
          />
          <NumberField
            label="Max open positions"
            optional
            min={0}
            value={v.max_open_positions}
            onChange={(x) => set("max_open_positions", x)}
          />
          <NumberField
            label="Max entries per day"
            optional
            min={0}
            value={v.max_trades_per_day}
            onChange={(x) => set("max_trades_per_day", x)}
          />
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" variant="secondary" disabled={busy}>
            Save limits
          </Button>
          {note && (
            <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
              {note.text}
            </p>
          )}
        </div>
      </form>
    </Card>
  );
}
