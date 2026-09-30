import type { AdminUserRow } from "@algoearning/api-types";
import { formatInr } from "@algoearning/shared";
import { Card, StatusPill, cn } from "@algoearning/ui";
import Link from "next/link";
import type { ReactNode } from "react";

const dateTime = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

export const fmtDateTime = (v: string | null | undefined) => (v ? dateTime.format(new Date(v)) : "–");
export const fmtDate = (v: string | null | undefined) => (v ? dateOnly.format(new Date(v)) : "–");
export const rupees = (paise: number) => formatInr(paise / 100);

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Card>
  );
}

export function UserStatus({ status }: { status: AdminUserRow["status"] }) {
  const tone = status === "active" ? "success" : status === "suspended" ? "danger" : "neutral";
  return <StatusPill tone={tone}>{status}</StatusPill>;
}

export function PlanPill({
  user,
}: {
  user: Pick<AdminUserRow, "plan_code" | "plan_name" | "has_overrides">;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusPill tone={user.plan_code === "free" ? "neutral" : "info"}>{user.plan_name}</StatusPill>
      {user.has_overrides && <StatusPill tone="warning">custom limits</StatusPill>}
    </span>
  );
}

export function UserCell({ user }: { user: Pick<AdminUserRow, "id" | "email" | "name" | "avatar_url"> }) {
  return (
    <Link href={`/monitor/users/${user.id}`} className="flex min-w-0 items-center gap-2.5 hover:underline">
      {user.avatar_url ? (
        // eslint-disable-next-line @next/next/no-img-element -- Clerk-hosted avatar
        <img src={user.avatar_url} alt="" className="size-7 shrink-0 rounded-full border border-border" />
      ) : (
        <span
          className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-semibold"
          aria-hidden
        >
          {(user.name ?? user.email).slice(0, 1).toUpperCase()}
        </span>
      )}
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{user.name ?? user.email}</span>
        {user.name && <span className="block truncate text-xs text-muted">{user.email}</span>}
      </span>
    </Link>
  );
}

/** A table that scrolls sideways on small screens instead of squashing. */
export function Table({
  head,
  children,
  className,
}: {
  head: string[];
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full min-w-[40rem] text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted">
            {head.map((h) => (
              <th key={h} scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  );
}

export const td = "px-3 py-2.5 align-middle";

export function LoadError({ what, message }: { what: string; message: string }) {
  return (
    <StatusPill tone="danger">
      Could not load {what}: {message}
    </StatusPill>
  );
}
