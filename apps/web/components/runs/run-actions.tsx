"use client";

import type { RiskSettings } from "@algoearning/api-types";
import { Button, Card, CardTitle, Switch, cn, useConfirm } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { LogOut, OctagonX, Pause, Play, Square } from "lucide-react";
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
  const confirm = useConfirm();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="danger"
        disabled={busy}
        onClick={async () => {
          const ok = await confirm({
            title: `Stop ${name}?`,
            message: "Its open positions are squared off now (on live runs, orders go to your broker).",
            confirmLabel: "Stop strategy",
            tone: "danger",
          });
          if (ok) await call("POST", `/v1/runs/${runId}/stop`);
        }}
      >
        <Square className="size-3.5" aria-hidden /> Stop
      </Button>
      {note && !note.ok && <span className="text-xs text-loss">{note.text}</span>}
    </span>
  );
}

/** "Enter now": start today's trade at once when its entry time was missed (live runs place real orders). */
export function EnterNowButton({ runId, name, live }: { runId: string; name: string; live: boolean }) {
  const { call, busy, note } = useCall();
  const confirm = useConfirm();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="secondary"
        disabled={busy}
        onClick={async () => {
          const ok = await confirm({
            title: `Enter ${name} now?`,
            message:
              "Today's trade is entered right away, outside its entry time, with the usual legs, lots and risk " +
              "checks." +
              (live ? " This is a live run: real orders go to your broker." : ""),
            confirmLabel: "Enter now",
            tone: live ? "danger" : undefined,
          });
          if (ok)
            await call("POST", `/v1/runs/${runId}/enter-now`, undefined, "Asked the engine to enter now.");
        }}
      >
        <Play className="size-3.5" aria-hidden /> Enter now
      </Button>
      {note && <span className={cn("text-xs", note.ok ? "text-muted" : "text-loss")}>{note.text}</span>}
    </span>
  );
}

/** "Exit now": square off the open positions at once; the strategy keeps running (Stop also stops it). */
export function ExitNowButton({ runId, name, live }: { runId: string; name: string; live: boolean }) {
  const { call, busy, note } = useCall();
  const confirm = useConfirm();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="secondary"
        disabled={busy}
        onClick={async () => {
          const ok = await confirm({
            title: `Exit ${name}'s open positions now?`,
            message:
              "Every open position of this strategy is squared off right away. The strategy keeps running and " +
              "takes its next trade as usual (Stop would also stop it)." +
              (live ? " This is a live run: real orders go to your broker." : ""),
            confirmLabel: "Exit now",
            tone: "danger",
          });
          if (ok)
            await call("POST", `/v1/runs/${runId}/exit-now`, undefined, "Asked the engine to exit now.");
        }}
      >
        <LogOut className="size-3.5" aria-hidden /> Exit now
      </Button>
      {note && <span className={cn("text-xs", note.ok ? "text-muted" : "text-loss")}>{note.text}</span>}
    </span>
  );
}

/** Pause: no new trade until resumed; open positions keep their stop-losses, targets, limits and exit time. */
export function PauseRunButton({ runId, name, paused }: { runId: string; name: string; paused: boolean }) {
  const { call, busy, note } = useCall();
  const confirm = useConfirm();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="secondary"
        disabled={busy}
        onClick={async () => {
          if (paused) {
            await call("POST", `/v1/runs/${runId}/resume`);
            return;
          }
          const ok = await confirm({
            title: `Pause ${name}?`,
            message:
              "It takes no new trade until you resume it. Positions already open stay open and keep their " +
              "stop-losses, targets, limits and exit time (use Stop to square them off).",
            confirmLabel: "Pause",
          });
          if (ok) await call("POST", `/v1/runs/${runId}/pause`);
        }}
      >
        {paused ? <Play className="size-3.5" aria-hidden /> : <Pause className="size-3.5" aria-hidden />}{" "}
        {paused ? "Resume" : "Pause"}
      </Button>
      {note && !note.ok && <span className="text-xs text-loss">{note.text}</span>}
    </span>
  );
}

export function StopAllButton({ count }: { count: number }) {
  const { call, busy } = useCall();
  const confirm = useConfirm();
  if (count === 0) return null;
  return (
    <Button
      variant="danger"
      disabled={busy}
      onClick={async () => {
        const ok = await confirm({
          title: `Stop all ${count} running ${count === 1 ? "strategy" : "strategies"}?`,
          message: "Every open position is squared off now (on live runs, orders go to your broker).",
          confirmLabel: "Stop all",
          tone: "danger",
        });
        if (ok) await call("POST", "/v1/runs/stop-all");
      }}
    >
      <OctagonX className="size-4" aria-hidden /> Stop all
    </Button>
  );
}

/** The user's own limits across all their runs; the engine checks them before every entry. */
export function RiskSettingsCard({ initial }: { initial: RiskSettings }) {
  const { call, busy, note } = useCall();
  const confirm = useConfirm();
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
            onCheckedChange={async (on) => {
              if (
                on &&
                !(await confirm({
                  title: "Turn on the kill switch?",
                  message:
                    "Every open position is squared off and no new entries are made until you turn it off.",
                  confirmLabel: "Turn on kill switch",
                  tone: "danger",
                }))
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
