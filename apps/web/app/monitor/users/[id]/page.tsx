import type { AdminUserDetail, Me, PlanAdmin } from "@algoearning/api-types";
import { Card, CardTitle, StatusPill } from "@algoearning/ui";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { LoadError, Table, UserStatus, fmtDate, fmtDateTime, td } from "@/components/monitor/bits";
import { AccountActions, EndGrantButton, LiveUnlock, PlanAndLimits } from "@/components/monitor/user-admin";
import { apiGet } from "@/lib/api";

export const metadata = { title: "User" };

export default async function MonitorUser({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const [r, plans, me] = await Promise.all([
    apiGet<AdminUserDetail>(`/v1/admin/users/${id}`),
    apiGet<PlanAdmin[]>("/v1/admin/plans"),
    apiGet<Me>("/v1/me"),
  ]);
  if (!r.ok && r.status === 404) notFound();
  if (!r.ok || !plans.ok)
    return (
      <LoadError
        what="the user"
        message={(!r.ok ? r.error : !plans.ok ? plans.error : null)?.message ?? ""}
      />
    );
  const d = r.data;
  const u = d.user;
  return (
    <div className="flex flex-col gap-4">
      <Link
        href="/monitor/users"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Users
      </Link>
      <Card className="flex flex-wrap items-center gap-4">
        {u.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- Clerk-hosted avatar
          <img src={u.avatar_url} alt="" className="size-14 rounded-full border border-border" />
        ) : (
          <span
            className="grid size-14 place-items-center rounded-full bg-surface-2 text-xl font-semibold"
            aria-hidden
          >
            {(u.name ?? u.email).slice(0, 1).toUpperCase()}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold">{u.name ?? u.email}</h1>
          <p className="truncate text-sm text-muted">{u.email}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted">
            <UserStatus status={u.status} />
            {u.role === "admin" && <StatusPill tone="warning">admin</StatusPill>}
            <span>Joined {fmtDate(u.created_at)}</span>
            <span>· Last seen {fmtDateTime(u.last_seen_at)}</span>
          </div>
        </div>
        <AccountActions detail={d} isSelf={me.ok && me.data.id === u.id} />
      </Card>

      <LiveUnlock detail={d} />

      <PlanAndLimits key={JSON.stringify(d.entitlements.overrides)} detail={d} plans={plans.data} />

      <Card className="p-0">
        <div className="px-5 pt-4 pb-2">
          <CardTitle>Subscriptions</CardTitle>
        </div>
        {d.subscriptions.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted">None: on the free plan.</p>
        ) : (
          <Table head={["Plan", "How", "Status", "From", "Until", ""]}>
            {d.subscriptions.map((x) => {
              const live =
                x.status === "active" &&
                x.current_period_end != null &&
                new Date(x.current_period_end) > new Date();
              return (
                <tr key={x.id}>
                  <td className={td}>{x.plan_name}</td>
                  <td className={td}>
                    {x.provider === "manual" ? <StatusPill tone="info">granted</StatusPill> : "paid"}
                  </td>
                  <td className={td}>{x.status}</td>
                  <td className={`${td} whitespace-nowrap text-muted`}>{fmtDate(x.current_period_start)}</td>
                  <td className={`${td} whitespace-nowrap text-muted`}>{fmtDate(x.current_period_end)}</td>
                  <td className={`${td} text-right`}>
                    {x.provider === "manual" && live && (
                      <EndGrantButton userId={u.id} subscriptionId={x.id} />
                    )}
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-0">
          <div className="px-5 pt-4 pb-2">
            <CardTitle>Broker accounts</CardTitle>
          </div>
          {d.broker_accounts.length === 0 ? (
            <p className="px-5 pb-5 text-sm text-muted">None.</p>
          ) : (
            <Table head={["Broker", "Client ID", "Status", "Engine", "Last login"]}>
              {d.broker_accounts.map((b) => (
                <tr key={b.id}>
                  <td className={td}>{b.broker}</td>
                  <td className={td}>
                    {b.client_id}
                    {b.label && <span className="text-muted"> · {b.label}</span>}
                  </td>
                  <td className={td}>
                    <StatusPill
                      tone={
                        b.status === "connected" ? "success" : b.status === "error" ? "danger" : "neutral"
                      }
                    >
                      {b.status}
                    </StatusPill>
                  </td>
                  <td className={td}>{b.engine_enabled ? "on" : "off"}</td>
                  <td className={`${td} whitespace-nowrap text-muted`}>{fmtDateTime(b.last_login_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Card className="p-0">
          <div className="flex items-center justify-between px-5 pt-4 pb-2">
            <CardTitle>Recent activity</CardTitle>
            <Link href={`/monitor/audit?user_id=${u.id}`} className="text-sm text-primary-text underline">
              Full log
            </Link>
          </div>
          {d.recent_activity.length === 0 ? (
            <p className="px-5 pb-5 text-sm text-muted">Nothing yet.</p>
          ) : (
            <ul className="divide-y divide-border px-5 pb-3 text-sm">
              {d.recent_activity.map((a) => (
                <li key={a.id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span>
                    <code className="text-xs">{a.action}</code>
                    {a.actor !== "user" && <span className="text-muted"> · by {a.actor}</span>}
                  </span>
                  <span className="text-xs whitespace-nowrap text-muted">{fmtDateTime(a.ts)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
