import type { Me, Run } from "@algoearning/api-types";
import { UserButton } from "@clerk/nextjs";
import { ThemeToggle } from "@algoearning/ui";
import { Bell } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { ApiStatus } from "@/components/api-status";
import { Logo } from "@/components/logo";
import { MarketTicker } from "@/components/market-ticker";
import { MobileNav } from "@/components/mobile-nav";
import { Sidebar } from "@/components/sidebar";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

/** The signed-in app shell (auth arrives in phase 4): sidebar + top bar + content. */
export default async function AppLayout({ children }: { children: ReactNode }) {
  await requireUser();
  const [me, runs] = await Promise.all([apiGet<Me>("/v1/me"), apiGet<Run[]>("/v1/runs")]);
  const isAdmin = me.ok && me.data.role === "admin";
  const liveRuns = runs.ok ? runs.data.filter((r) => r.mode === "live" && !r.dry_run).length : 0;
  return (
    <div className="flex min-h-dvh">
      <Sidebar isAdmin={isAdmin} />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end gap-2 border-b border-border bg-surface px-4">
          <span className="mr-auto md:hidden">
            <Logo />
          </span>
          <span className="mr-auto hidden md:inline-flex">
            <MarketTicker />
          </span>
          <span className="hidden sm:inline-flex">
            <ApiStatus />
          </span>
          <ThemeToggle />
          <Link
            href="/notifications"
            aria-label="Notifications"
            className="rounded-lg p-2 text-muted hover:bg-surface-2"
          >
            <Bell className="size-[18px]" aria-hidden />
          </Link>
          <UserButton />
        </header>
        <MobileNav isAdmin={isAdmin} />
        {liveRuns > 0 && (
          <Link
            href="/runs"
            role="status"
            className="flex items-center justify-center gap-2 bg-loss px-4 py-1.5 text-sm font-medium text-white"
          >
            LIVE: {liveRuns} strateg{liveRuns === 1 ? "y is" : "ies are"} placing real orders
          </Link>
        )}
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
