import type { AuditPage } from "@algoearning/api-types";
import { Card } from "@algoearning/ui";
import Link from "next/link";

import { inputClass } from "@/components/builder/fields";
import { LoadError, Table, fmtDateTime, td } from "@/components/monitor/bits";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Audit log" };

export default async function MonitorAudit({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const qs = new URLSearchParams();
  for (const k of ["action", "user_id", "cursor"] as const) if (sp[k]) qs.set(k, sp[k]!);
  const r = await apiGet<AuditPage>(`/v1/admin/audit?${qs}`);
  const next = new URLSearchParams(qs);
  if (r.ok && r.data.next_cursor) next.set("cursor", r.data.next_cursor);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="text-sm text-muted">
          Who did what, newest first. Entries can never be changed or deleted.
        </p>
      </div>
      <Card className="flex flex-col gap-4">
        <form className="flex flex-wrap gap-2" action="/monitor/audit">
          <input
            name="action"
            defaultValue={sp.action}
            placeholder="Action starts with, e.g. admin. or strategy."
            aria-label="Action starts with"
            className={`${inputClass} max-w-sm flex-1`}
          />
          {sp.user_id && <input type="hidden" name="user_id" value={sp.user_id} />}
          <button
            type="submit"
            className="h-9 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            Filter
          </button>
          {(sp.action || sp.user_id) && (
            <Link href="/monitor/audit" className="self-center text-sm text-primary-text underline">
              Clear
            </Link>
          )}
        </form>
        {!r.ok ? (
          <LoadError what="the audit log" message={r.error.message} />
        ) : (
          <Table head={["When", "Who", "Action", "Target", "Details", "IP"]}>
            {r.data.items.map((a) => (
              <tr key={a.id}>
                <td className={`${td} whitespace-nowrap text-muted`}>{fmtDateTime(a.ts)}</td>
                <td className={`${td} max-w-52`}>
                  {a.user_id ? (
                    <Link href={`/monitor/users/${a.user_id}`} className="block truncate hover:underline">
                      {a.user_email ?? a.user_id}
                    </Link>
                  ) : (
                    <span className="text-muted">system</span>
                  )}
                  {a.actor !== "user" && <span className="text-xs text-muted">as {a.actor}</span>}
                </td>
                <td className={td}>
                  <code className="text-xs">{a.action}</code>
                </td>
                <td className={`${td} text-xs text-muted`}>
                  {a.target_type ? `${a.target_type} ${a.target_id ?? ""}` : "–"}
                </td>
                <td className={`${td} max-w-80`}>
                  {Object.keys(a.detail).length > 0 && (
                    <code className="block truncate text-xs text-muted" title={JSON.stringify(a.detail)}>
                      {JSON.stringify(a.detail)}
                    </code>
                  )}
                </td>
                <td className={`${td} text-xs text-muted`}>{a.ip ?? "–"}</td>
              </tr>
            ))}
          </Table>
        )}
        {r.ok && r.data.next_cursor && (
          <Link href={`/monitor/audit?${next}`} className="self-end text-sm text-primary-text underline">
            Older
          </Link>
        )}
      </Card>
    </div>
  );
}
