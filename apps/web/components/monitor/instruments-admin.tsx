"use client";

import type { InstrumentAdmin, InstrumentRefresh } from "@algoearning/api-types";
import { Button, StatusPill, Switch, cn } from "@algoearning/ui";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { inputClass } from "@/components/builder/fields";

import { Table, fmtDateTime, td } from "./bits";
import { useAdminAction } from "./use-admin-action";

function Row({ i }: { i: InstrumentAdmin }) {
  const { run, busy, note } = useAdminAction();
  const [open, setOpen] = useState(i.session_open);
  const [close, setClose] = useState(i.session_close);
  const [freeze, setFreeze] = useState(i.freeze_qty);
  const dirty = open !== i.session_open || close !== i.session_close || freeze !== i.freeze_qty;
  return (
    <tr>
      <td className={td}>
        <p className="font-medium">{i.code}</p>
        <p className="text-xs text-muted">
          {i.name} · {i.exchange}
        </p>
      </td>
      <td className={`${td} tabular-nums`}>{i.lot_size}</td>
      <td className={`${td} tabular-nums`}>{i.strike_step}</td>
      <td className={td}>{i.weekly_expiry ? "weekly" : "monthly"}</td>
      <td className={td}>
        <input
          type="number"
          min={1}
          aria-label={`${i.code} freeze quantity`}
          className={`${inputClass} w-24`}
          value={freeze}
          onChange={(e) => setFreeze(Math.max(1, Math.trunc(e.target.valueAsNumber || 1)))}
        />
      </td>
      <td className={td}>
        <div className="flex items-center gap-1.5">
          <input
            type="time"
            aria-label={`${i.code} opens`}
            className={`${inputClass} w-28`}
            value={open}
            onChange={(e) => setOpen(e.target.value)}
          />
          <span className="text-muted">–</span>
          <input
            type="time"
            aria-label={`${i.code} closes`}
            className={`${inputClass} w-28`}
            value={close}
            onChange={(e) => setClose(e.target.value)}
          />
          {dirty && (
            <Button
              size="sm"
              disabled={busy !== null}
              onClick={() =>
                run(
                  "hours",
                  "PATCH",
                  `/v1/admin/instruments/${i.code}`,
                  { session_open: open, session_close: close, freeze_qty: freeze },
                  "Saved.",
                )
              }
            >
              Save
            </Button>
          )}
        </div>
        {note && <p className={cn("mt-1 text-xs", note.ok ? "text-profit" : "text-loss")}>{note.text}</p>}
      </td>
      <td className={td}>
        <Switch
          checked={i.is_active}
          disabled={busy !== null}
          aria-label={`${i.code} available for new strategies`}
          onCheckedChange={(on) =>
            run("active", "PATCH", `/v1/admin/instruments/${i.code}`, { is_active: on })
          }
        />
      </td>
      <td className={`${td} text-xs text-muted`}>
        {i.source === "kite" ? `Zerodha, ${fmtDateTime(i.refreshed_at)}` : i.source}
      </td>
    </tr>
  );
}

export function InstrumentsAdmin({ instruments }: { instruments: InstrumentAdmin[] }) {
  const { run, busy, note } = useAdminAction();
  const [result, setResult] = useState<InstrumentRefresh | null>(null);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          disabled={busy !== null}
          onClick={async () =>
            setResult(
              (await run<InstrumentRefresh>("refresh", "POST", "/v1/admin/instruments/refresh")) ?? null,
            )
          }
        >
          <RefreshCw className={cn("size-4", busy === "refresh" && "animate-spin")} aria-hidden /> Refresh
          from Zerodha now
        </Button>
        {note && !note.ok && <p className="text-sm text-loss">{note.text}</p>}
        {result && (
          <p role="status" className="text-sm">
            {Object.keys(result.changed).length === 0 ? (
              <StatusPill tone="success">No changes</StatusPill>
            ) : (
              Object.entries(result.changed).map(([code, d]) => (
                <StatusPill key={code} tone="warning">
                  {code}:{" "}
                  {Object.entries(d)
                    .map(([k, [a, b]]) => `${k.replace("_", " ")} ${String(a)} → ${String(b)}`)
                    .join(", ")}
                </StatusPill>
              ))
            )}
            {result.missing.length > 0 && (
              <span className="ml-2 text-warning">Not in the list: {result.missing.join(", ")}</span>
            )}
          </p>
        )}
      </div>
      <Table
        head={[
          "Instrument",
          "Lot size",
          "Strike step",
          "Expiries",
          "Max qty per order",
          "Trading hours (IST)",
          "Available",
          "Last update",
        ]}
      >
        {instruments.map((i) => (
          <Row key={`${i.code}:${i.updated_at}`} i={i} />
        ))}
      </Table>
    </div>
  );
}
