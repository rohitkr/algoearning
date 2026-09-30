import type { AdminUserPage } from "@algoearning/api-types";
import { Card, StatusPill } from "@algoearning/ui";
import { Search } from "lucide-react";
import Link from "next/link";

import {
  LoadError,
  PlanPill,
  Table,
  UserCell,
  UserStatus,
  fmtDate,
  fmtDateTime,
  td,
} from "@/components/monitor/bits";
import { inputClass } from "@/components/builder/fields";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Users" };

export default async function MonitorUsers({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const qs = new URLSearchParams();
  for (const k of ["q", "status", "role", "cursor"] as const) if (sp[k]) qs.set(k, sp[k]!);
  const r = await apiGet<AdminUserPage>(`/v1/admin/users?${qs}`);
  const next = new URLSearchParams(qs);
  if (r.ok && r.data.next_cursor) next.set("cursor", r.data.next_cursor);
  const first = new URLSearchParams(qs);
  first.delete("cursor");

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Users</h1>
      <Card className="flex flex-col gap-4">
        <form className="flex flex-wrap items-end gap-2" role="search" action="/monitor/users">
          <label className="relative min-w-56 flex-1">
            <span className="sr-only">Search by email or name</span>
            <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" aria-hidden />
            <input
              name="q"
              type="search"
              defaultValue={sp.q}
              placeholder="Email or name"
              className={`${inputClass} pl-8`}
            />
          </label>
          <label className="text-xs text-muted">
            <span className="sr-only">Status</span>
            <select name="status" defaultValue={sp.status ?? ""} className={inputClass}>
              <option value="">Any status</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
              <option value="deleted">Deleted</option>
            </select>
          </label>
          <label className="text-xs text-muted">
            <span className="sr-only">Role</span>
            <select name="role" defaultValue={sp.role ?? ""} className={inputClass}>
              <option value="">Any role</option>
              <option value="user">Users</option>
              <option value="admin">Admins</option>
            </select>
          </label>
          <button
            type="submit"
            className="h-9 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            Search
          </button>
        </form>

        {!r.ok ? (
          <LoadError what="users" message={r.error.message} />
        ) : r.data.items.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
            No users match.
          </p>
        ) : (
          <Table head={["User", "Plan", "Role", "Status", "Strategies", "Brokers", "Joined", "Last seen"]}>
            {r.data.items.map((u) => (
              <tr key={u.id}>
                <td className={`${td} max-w-64`}>
                  <UserCell user={u} />
                </td>
                <td className={td}>
                  <PlanPill user={u} />
                </td>
                <td className={td}>
                  {u.role === "admin" ? <StatusPill tone="warning">admin</StatusPill> : "user"}
                </td>
                <td className={td}>
                  <UserStatus status={u.status} />
                </td>
                <td className={`${td} tabular-nums`}>{u.strategies}</td>
                <td className={`${td} tabular-nums`}>{u.broker_accounts}</td>
                <td className={`${td} whitespace-nowrap text-muted`}>{fmtDate(u.created_at)}</td>
                <td className={`${td} whitespace-nowrap text-muted`}>{fmtDateTime(u.last_seen_at)}</td>
              </tr>
            ))}
          </Table>
        )}
        {r.ok && (sp.cursor || r.data.next_cursor) && (
          <div className="flex justify-between text-sm">
            {sp.cursor ? (
              <Link href={`/monitor/users?${first}`} className="text-primary-text underline">
                Back to first page
              </Link>
            ) : (
              <span />
            )}
            {r.data.next_cursor && (
              <Link href={`/monitor/users?${next}`} className="text-primary-text underline">
                More
              </Link>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
