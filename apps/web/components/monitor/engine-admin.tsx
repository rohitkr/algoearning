"use client";

import type { EngineStatus } from "@algoearning/api-types";
import { Button, Card, CardTitle, StatusPill, cn, useConfirm } from "@algoearning/ui";
import { useState } from "react";

import { inputClass } from "@/components/builder/fields";

import { fmtDateTime } from "./bits";
import { useAdminAction } from "./use-admin-action";

/** The platform kill switch: squares off every user's running strategies and blocks new entries until lifted. */
export function EngineControl({ status }: { status: EngineStatus }) {
  const { run, busy, note } = useAdminAction();
  const confirm = useConfirm();
  const [reason, setReason] = useState("");
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Trading</CardTitle>
        {status.trading_halted ? (
          <StatusPill tone="danger">Halted{status.halt_reason ? `: ${status.halt_reason}` : ""}</StatusPill>
        ) : (
          <StatusPill tone="success">Allowed</StatusPill>
        )}
      </div>
      <p className="text-sm text-muted">
        {status.active_runs} active run{status.active_runs === 1 ? "" : "s"} · engine last stepped a run{" "}
        {fmtDateTime(status.last_heartbeat)}
      </p>
      {status.trading_halted ? (
        <Button
          className="w-fit"
          disabled={busy !== null}
          onClick={() =>
            run("halt", "PUT", "/v1/admin/engine/halt", { halted: false }, "Trading allowed again.")
          }
        >
          Allow trading again
        </Button>
      ) : (
        <form
          className="flex flex-wrap gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const ok = await confirm({
              title: "Halt all trading?",
              message:
                "Every user's open positions are squared off and new entries are blocked until you allow trading again.",
              confirmLabel: "Halt all trading",
              tone: "danger",
            });
            if (!ok) return;
            void run(
              "halt",
              "PUT",
              "/v1/admin/engine/halt",
              { halted: true, reason: reason || null },
              "Trading halted.",
            );
          }}
        >
          <input
            aria-label="Reason"
            placeholder="Reason (shown to users)"
            className={`${inputClass} max-w-xs flex-1`}
            value={reason}
            maxLength={300}
            onChange={(e) => setReason(e.target.value)}
          />
          <Button type="submit" variant="danger" disabled={busy !== null}>
            Halt all trading
          </Button>
        </form>
      )}
      {note && (
        <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
          {note.text}
        </p>
      )}
    </Card>
  );
}
