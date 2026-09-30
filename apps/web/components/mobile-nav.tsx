"use client";

import { cn } from "@algoearning/ui";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV, isActive } from "./nav";

/** Below md the sidebar is hidden: the same destinations as a horizontally scrollable row. */
export function MobileNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Main"
      className="flex gap-1 overflow-x-auto border-b border-border bg-surface px-2 py-2 md:hidden"
    >
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-sm",
              active ? "bg-primary text-primary-foreground" : "text-muted hover:bg-surface-2",
            )}
          >
            <Icon className="size-4" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
