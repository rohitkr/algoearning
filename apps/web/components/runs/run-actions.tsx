"use client";

import type { RiskSettings } from "@algoearning/api-types";
import { Button, Card, CardTitle, Switch, cn, useConfirm } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Hand, LogOut, OctagonX, Pause, Play, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

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

/** Stop. With positions open it asks what to do with them: square them off (orders to the broker), or keep them in
 * Zerodha for the user to manage (no order is sent). */
export function StopRunButton({ runId, name, open = 0 }: { runId: string; name: string; open?: number }) {
  const { call, busy, note } = useCall();
  const confirm = useConfirm();
  const ref = useRef<HTMLDialogElement>(null);
  const close = () => ref.current?.close();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="danger"
        disabled={busy}
        onClick={async () => {
          if (open > 0) {
            ref.current?.showModal();
            return;
          }
          const ok = await confirm({
            title: `Stop ${name}?`,
            message:
              "Nothing is open, so no order is sent. The strategy takes no new trade until deployed again.",
            confirmLabel: "Stop strategy",
            tone: "danger",
          });
          if (ok) await call("POST", `/v1/runs/${runId}/stop`);
        }}
      >
        <Square className="size-3.5" aria-hidden /> Stop
      </Button>
      {note && !note.ok && <span className="text-xs text-loss">{note.text}</span>}
      <dialog
        ref={ref}
        className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-border bg-surface p-5 text-foreground backdrop:bg-black/50"
        onClick={(e) => e.target === ref.current && close()}
      >
        <h2 className="text-lg font-semibold">Stop {name}?</h2>
        <p className="mt-1.5 text-sm text-muted">
          It has {open} open position{open === 1 ? "" : "s"}. What should happen to{" "}
          {open === 1 ? "it" : "them"}?
        </p>
        <div className="mt-4 flex flex-col gap-2">
          <Button
            variant="danger"
            onClick={async () => {
              close();
              await call("POST", `/v1/runs/${runId}/stop`);
            }}
          >
            Square off and stop
          </Button>
          <p className="-mt-1 mb-1 text-xs text-muted">
            The app closes everything it opened (live runs: orders go to your broker), then stops.
          </p>
          <Button
            variant="secondary"
            onClick={async () => {
              close();
              await call("POST", `/v1/runs/${runId}/release`);
            }}
          >
            Stop, keep positions in Zerodha
          </Button>
          <p className="-mt-1 mb-1 text-xs text-muted">
            No order is sent. The legs stay in Zerodha for you to manage in Kite (use this when you already
            exited some legs yourself, or hold the same strikes manually).
          </p>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
        </div>
      </dialog>
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
              "Everything stops until you resume it: no entry and no exit — open positions are not watched (no " +
              "stop-loss, target or exit time) while paused. Resume picks up again from the current prices (an exit " +
              "time that passed meanwhile exits at once). Exit now still works if you need to get out.",
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

/** Stop without squaring off: the open legs stay in Zerodha for the user to manage by hand (no order is sent). */
export function ReleaseRunButton({
  runId,
  name,
  stopping,
}: {
  runId: string;
  name: string;
  stopping: boolean;
}) {
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
            title: stopping ? `Stop squaring off ${name}?` : `Stop ${name} and keep its positions?`,
            message:
              "The strategy stops and sends no order. Its open legs stay in Zerodha as they are, for you to manage " +
              "by hand in Kite (the app books them as closed at the current price). Use this when you have already " +
              "exited some legs yourself, or hold the same strikes manually.",
            confirmLabel: "Stop, keep positions",
          });
          if (ok)
            await call("POST", `/v1/runs/${runId}/release`, undefined, "Stopping without squaring off.");
        }}
      >
        <Hand className="size-3.5" aria-hidden /> {stopping ? "Stop squaring off" : "Stop, keep positions"}
      </Button>
      {note && <span className={cn("text-xs", note.ok ? "text-muted" : "text-loss")}>{note.text}</span>}
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
