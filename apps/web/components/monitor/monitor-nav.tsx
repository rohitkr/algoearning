"use client";

import { cn } from "@algoearning/ui";
import { Activity, CandlestickChart, Gauge, Layers, RadioTower, Users } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/monitor", label: "Overview", icon: Gauge, exact: true },
  { href: "/monitor/users", label: "Users", icon: Users },
  { href: "/monitor/plans", label: "Plans", icon: Layers },
  { href: "/monitor/instruments", label: "Instruments", icon: CandlestickChart },
  { href: "/monitor/market-data", label: "Market data", icon: RadioTower },
  { href: "/monitor/audit", label: "Audit log", icon: Activity },
];

export function MonitorNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Monitor" className="flex gap-1 overflow-x-auto">
      {ITEMS.map(({ href, label, icon: Icon, exact }) => {
        const active = exact ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-2 rounded-lg px-3 py-1.5 text-sm",
              active
                ? "bg-accent font-medium text-primary-text"
                : "text-muted hover:bg-surface-2 hover:text-foreground",
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
