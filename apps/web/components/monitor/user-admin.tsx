"use client";

import type { AdminUserDetail, PlanAdmin } from "@algoearning/api-types";
import { Button, Card, CardTitle, Meter, StatusPill, cn } from "@algoearning/ui";
import { useState } from "react";

import { inputClass } from "@/components/builder/fields";

import { fmtDate } from "./bits";
import { FeatureEditor } from "./feature-editor";
import { useAdminAction } from "./use-admin-action";

const USAGE: Record<string, string> = {
  max_strategies: "Saved strategies",
  max_running_strategies: "Running strategies",
  max_broker_accounts: "Broker accounts",
};

function Note({ note, k }: { note: { key: string; ok: boolean; text: string } | null; k: string }) {
  if (note?.key !== k) return null;
  return (
    <p role="status" className={cn("text-sm", note.ok ? "text-profit" : "text-loss")}>
      {note.text}
    </p>
  );
}

/** Account actions: status and role. */
export function AccountActions({ detail, isSelf }: { detail: AdminUserDetail; isSelf: boolean }) {
  const { run, busy, note } = useAdminAction();
  const u = detail.user;
  const path = `/v1/admin/users/${u.id}`;
  if (u.status === "deleted") return <StatusPill>Deleted by the user</StatusPill>;
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap justify-end gap-2">
        {u.status === "active" ? (
          <Button
            size="sm"
            variant="danger"
            disabled={isSelf || busy !== null}
            onClick={() =>
              confirm(`Suspend ${u.email}? They are signed out of the app and API at once.`) &&
              run("account", "PATCH", path, { status: "suspended" }, "Suspended.")
            }
          >
            Suspend
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={busy !== null}
            onClick={() => run("account", "PATCH", path, { status: "active" }, "Reactivated.")}
          >
            Reactivate
          </Button>
        )}
        <Button
          size="sm"
          variant="secondary"
          disabled={isSelf || busy !== null}
          onClick={() => {
            const role = u.role === "admin" ? "user" : "admin";
            if (
              confirm(
                role === "admin"
                  ? `Make ${u.email} an admin? They get full access to Monitor.`
                  : `Remove admin access from ${u.email}?`,
              )
            )
              void run(
                "account",
                "PATCH",
                path,
                { role },
                role === "admin" ? "Now an admin." : "Admin access removed.",
              );
          }}
        >
          {u.role === "admin" ? "Remove admin" : "Make admin"}
        </Button>
      </div>
      {isSelf && <p className="text-xs text-muted">Another admin must change your own account.</p>}
      <Note note={note} k="account" />
    </div>
  );
}

/** Plan, limits (with per-user overrides) and complimentary plans. */
export function PlanAndLimits({ detail, plans }: { detail: AdminUserDetail; plans: PlanAdmin[] }) {
  const { run, busy, note } = useAdminAction();
  const e = detail.entitlements;
  const u = detail.user;
  const [overrides, setOverrides] = useState<Record<string, boolean | number | null>>(
    e.overrides as Record<string, boolean | number | null>,
  );
  const [reason, setReason] = useState(detail.override_note ?? "");
  const [grantPlan, setGrantPlan] = useState(plans.find((p) => p.code !== "free")?.code ?? "");
  const [days, setDays] = useState(30);
  const planValues = (plans.find((p) => p.code === e.plan_code)?.features ?? {}) as Record<
    string,
    boolean | number | null
  >;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Plan and usage</CardTitle>
          <StatusPill tone={e.plan_code === "free" ? "neutral" : "info"}>{e.plan_name}</StatusPill>
        </div>
        <p className="text-sm text-muted">
          {e.current_period_end ? `Until ${fmtDate(e.current_period_end)}` : "Free plan, no end date"}
          {e.subscription_status ? ` · ${e.subscription_status}` : ""}
        </p>
        {Object.entries(e.usage).map(([k, x]) => (
          <Meter key={k} label={USAGE[k] ?? k} used={x.used} limit={x.limit ?? null} />
        ))}
        <form
          className="mt-2 flex flex-col gap-2 border-t border-border pt-4"
          onSubmit={(ev) => {
            ev.preventDefault();
            void run(
              "grant",
              "POST",
              `/v1/admin/users/${u.id}/grants`,
              { plan_code: grantPlan, days },
              `Granted for ${days} days.`,
            );
          }}
        >
          <p className="text-sm font-medium">Give a plan free of charge</p>
          <div className="flex flex-wrap gap-2">
            <select
              aria-label="Plan"
              className={`${inputClass} w-auto flex-1`}
              value={grantPlan}
              onChange={(ev) => setGrantPlan(ev.target.value)}
            >
              {plans
                .filter((p) => p.code !== "free")
                .map((p) => (
                  <option key={p.code} value={p.code}>
                    {p.name}
                    {p.is_active ? "" : " (not on sale)"}
                  </option>
                ))}
            </select>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="number"
                min={1}
                max={3660}
                aria-label="Days"
                className={`${inputClass} w-24`}
                value={days}
                onChange={(ev) => setDays(Math.max(1, Math.trunc(ev.target.valueAsNumber || 1)))}
              />
              days
            </label>
            <Button type="submit" size="md" disabled={busy !== null || !grantPlan}>
              Grant
            </Button>
          </div>
          <Note note={note} k="grant" />
        </form>
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <CardTitle>Custom limits for this user</CardTitle>
          <p className="mt-1 text-sm text-muted">
            Override single features on top of the plan, for testers or special cases. They stay until you
            remove them, whatever plan the user is on.
          </p>
        </div>
        <FeatureEditor
          catalog={e.catalog}
          value={overrides}
          onChange={setOverrides}
          mode="override"
          planValues={planValues}
        />
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          Reason (admins only)
          <input
            className={inputClass}
            value={reason}
            maxLength={500}
            onChange={(ev) => setReason(ev.target.value)}
            placeholder="e.g. beta tester"
          />
        </label>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={busy !== null}
            onClick={() =>
              run(
                "overrides",
                "PUT",
                `/v1/admin/users/${u.id}/overrides`,
                { features: overrides, note: reason || null },
                "Limits saved.",
              )
            }
          >
            Save limits
          </Button>
          {Object.keys(e.overrides).length > 0 && (
            <Button
              variant="secondary"
              disabled={busy !== null}
              onClick={() => {
                setOverrides({});
                void run(
                  "overrides",
                  "PUT",
                  `/v1/admin/users/${u.id}/overrides`,
                  { features: {}, note: null },
                  "Back to the plan's limits.",
                );
              }}
            >
              Remove all
            </Button>
          )}
        </div>
        <Note note={note} k="overrides" />
      </Card>
    </div>
  );
}

export function EndGrantButton({ userId, subscriptionId }: { userId: string; subscriptionId: string }) {
  const { run, busy, note } = useAdminAction();
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        variant="ghost"
        disabled={busy !== null}
        onClick={() =>
          confirm("End this granted plan now?") &&
          run("end", "DELETE", `/v1/admin/users/${userId}/grants/${subscriptionId}`)
        }
      >
        End now
      </Button>
      {note && !note.ok && <span className="text-xs text-loss">{note.text}</span>}
    </span>
  );
}
