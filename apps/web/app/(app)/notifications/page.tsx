import type { NotificationItem, NotificationSettings } from "@algoearning/api-types";
import { Card, CardTitle, StatusPill } from "@algoearning/ui";

import { NotificationSettingsForm } from "@/components/notifications/notification-settings";
import { AutoRefresh } from "@/components/runs/auto-refresh";
import { fmt } from "@/components/runs/run-bits";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Notifications" };

const TONE = { sent: "success", failed: "danger", pending: "info", skipped: "neutral" } as const;

export default async function NotificationsPage() {
  await requireUser();
  const [settings, history] = await Promise.all([
    apiGet<NotificationSettings>("/v1/me/notifications"),
    apiGet<NotificationItem[]>("/v1/me/notifications/history"),
  ]);
  if (!settings.ok)
    return <StatusPill tone="danger">Could not load notifications: {settings.error.message}</StatusPill>;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <AutoRefresh seconds={10} />
      <div>
        <h1 className="text-2xl font-semibold">Notifications</h1>
        <p className="text-sm text-muted">Choose how you are told, and about what.</p>
      </div>
      <NotificationSettingsForm initial={settings.data} />
      {history.ok && history.data.length > 0 && (
        <Card className="flex flex-col gap-2">
          <CardTitle>Recent</CardTitle>
          <ul className="divide-y divide-border text-sm">
            {history.data.map((n) => (
              <li key={n.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0">
                  <span className="font-medium">{n.title}</span>
                  <span className="block truncate text-xs text-muted">
                    {n.error ?? n.body.split("\n")[0]}
                  </span>
                </span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  {fmt(n.created_at)}
                  <StatusPill tone={TONE[n.status as keyof typeof TONE] ?? "neutral"}>
                    {n.status === "sent" ? `sent · ${n.sent_via.join(", ")}` : n.status}
                  </StatusPill>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
