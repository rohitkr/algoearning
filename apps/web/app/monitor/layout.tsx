import type { Me } from "@algoearning/api-types";
import { StatusPill, ThemeToggle } from "@algoearning/ui";
import { UserButton } from "@clerk/nextjs";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { Logo } from "@/components/logo";
import { MonitorNav } from "@/components/monitor/monitor-nav";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: { default: "Monitor", template: "%s · Monitor" } };

/** Monitor: the admin panel (monitor.<domain> in production, /monitor locally). Non-admins get a 404, so the
 * panel does not even reveal that it exists; the API checks the admin role again on every call. */
export default async function MonitorLayout({ children }: { children: ReactNode }) {
  await requireUser();
  const me = await apiGet<Me>("/v1/me");
  if (!me.ok || me.data.role !== "admin") notFound();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
          <Logo />
          <StatusPill tone="warning">Monitor</StatusPill>
          <span className="ml-auto" />
          <Link
            href="/dashboard"
            className="hidden items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 sm:inline-flex"
          >
            <ArrowLeft className="size-4" aria-hidden /> Back to app
          </Link>
          <ThemeToggle />
          <UserButton />
        </div>
        <div className="mx-auto max-w-7xl px-4 pb-2">
          <MonitorNav />
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-8">{children}</main>
    </div>
  );
}
