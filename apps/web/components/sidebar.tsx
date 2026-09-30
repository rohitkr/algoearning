"use client";

import { cn } from "@algoearning/ui";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Logo } from "./logo";
import { ADMIN_NAV, NAV, isActive } from "./nav";
import { UserCard } from "./user-card";

export function Sidebar({ isAdmin = false }: { isAdmin?: boolean }) {
  const pathname = usePathname();
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-4 md:flex">
      <div className="px-2 pb-6">
        <Logo />
      </div>
      <nav aria-label="Main" className="flex flex-col gap-1">
        {(isAdmin ? [...NAV, ADMIN_NAV] : NAV).map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted hover:bg-surface-2 hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-border pt-3">
        <UserCard />
      </div>
    </aside>
  );
}
