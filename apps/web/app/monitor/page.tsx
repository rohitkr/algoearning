import type { Overview } from "@algoearning/api-types";
import { Card, CardTitle } from "@algoearning/ui";
import Link from "next/link";

import {
  LoadError,
  PlanPill,
  Stat,
  Table,
  UserCell,
  fmtDate,
  fmtDateTime,
  rupees,
  td,
} from "@/components/monitor/bits";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Overview" };

export default async function MonitorOverview() {
  const r = await apiGet<Overview>("/v1/admin/overview");
  if (!r.ok) return <LoadError what="the overview" message={r.error.message} />;
  const o = r.data;
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Overview</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Users" value={o.users} sub={`${o.new_users_7d} new this week`} />
        <Stat label="Active this week" value={o.active_users} sub={`${o.suspended_users} suspended`} />
        <Stat label="On a paid plan" value={o.paying_users} sub="paid or granted" />
        <Stat label="Revenue, last 30 days" value={rupees(o.revenue_30d_paise)} />
        <Stat label="Strategies" value={o.strategies} sub={`${o.ready_strategies} ready`} />
        <Stat
          label="Broker accounts"
          value={o.broker_accounts}
          sub={`${o.connected_broker_accounts} connected now`}
        />
        <Stat
          label="Instruments refreshed"
          value={<span className="text-base">{fmtDateTime(o.instruments_refreshed_at)}</span>}
          sub={
            <Link href="/monitor/instruments" className="underline">
              Lot sizes and hours
            </Link>
          }
        />
        <Stat label="Engine" value={<span className="text-base text-muted">Arrives in P9</span>} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-0">
          <div className="flex items-center justify-between px-5 pt-4 pb-2">
            <CardTitle>Newest users</CardTitle>
            <Link href="/monitor/users" className="text-sm text-primary-text underline">
              All users
            </Link>
          </div>
          <Table head={["User", "Plan", "Joined"]}>
            {o.recent_users.map((u) => (
              <tr key={u.id}>
                <td className={td}>
                  <UserCell user={u} />
                </td>
                <td className={td}>
                  <PlanPill user={u} />
                </td>
                <td className={`${td} whitespace-nowrap text-muted`}>{fmtDate(u.created_at)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card className="p-0">
          <div className="px-5 pt-4 pb-2">
            <CardTitle>Latest payments</CardTitle>
          </div>
          {o.recent_payments.length === 0 ? (
            <p className="px-5 pb-5 text-sm text-muted">No payments yet.</p>
          ) : (
            <Table head={["User", "Plan", "Amount", "Paid"]}>
              {o.recent_payments.map((p) => (
                <tr key={p.order_id}>
                  <td className={`${td} max-w-48 truncate`}>{p.user_email}</td>
                  <td className={td}>{p.plan_name}</td>
                  <td className={`${td} tabular-nums`}>{rupees(p.amount_paise)}</td>
                  <td className={`${td} whitespace-nowrap text-muted`}>{fmtDateTime(p.paid_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
