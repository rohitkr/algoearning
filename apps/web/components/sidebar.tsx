"use client";

import { Tooltip, cn } from "@algoearning/ui";
import { ChevronLeft, ChevronRight, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Logo } from "./logo";
import { ADMIN_NAV, NAV, isActive } from "./nav";
import { UserCard } from "./user-card";

const KEY = "ae-sidebar";
const MIN = 200;
const MAX = 360;
const RAIL = 64; // collapsed: icons only
const DEFAULT = 240;

function remembered(): { width: number; collapsed: boolean } {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "{}") as { width?: number; collapsed?: boolean };
    return { width: Math.min(MAX, Math.max(MIN, v.width ?? DEFAULT)), collapsed: !!v.collapsed };
  } catch {
    return { width: DEFAULT, collapsed: false };
  }
}

/** The side menu: fixed while the page scrolls, collapsible to an icon rail, and resizable by dragging its edge.
 * Its width is published as --sidebar-w so the fixed header can line up with it. */
export function Sidebar({ isAdmin = false }: { isAdmin?: boolean }) {
  const pathname = usePathname();
  const [width, setWidth] = useState(DEFAULT);
  const [collapsed, setCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const loaded = useRef(false);

  useEffect(() => {
    const r = remembered();
    /* eslint-disable react-hooks/set-state-in-effect -- localStorage is only readable after mount (no hydration mismatch) */
    setWidth(r.width);
    setCollapsed(r.collapsed);
    /* eslint-enable react-hooks/set-state-in-effect */
    loaded.current = true;
  }, []);

  const shown = collapsed ? RAIL : width;
  useEffect(() => {
    document.documentElement.style.setProperty("--sidebar-w", `${shown}px`);
    if (!loaded.current) return;
    try {
      localStorage.setItem(KEY, JSON.stringify({ width, collapsed }));
    } catch {
      /* private mode: the size just is not remembered */
    }
  }, [shown, width, collapsed]);

  function drag(e: React.PointerEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const start = width;
    setDragging(true);
    const move = (m: PointerEvent) => setWidth(Math.min(MAX, Math.max(MIN, start + m.clientX - startX)));
    const up = () => {
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <aside
      style={{ width: shown }}
      className={cn(
        "sticky top-0 z-50 hidden h-dvh shrink-0 flex-col border-r border-border bg-surface py-4 md:flex",
        collapsed ? "px-2" : "px-3",
        !dragging && "transition-[width] duration-200",
      )}
    >
      <div className={cn("flex items-center pb-6", collapsed ? "justify-center" : "justify-between px-2")}>
        {collapsed ? (
          <Link
            href="/dashboard"
            aria-label="AlgoEarning: go to the dashboard"
            className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground"
          >
            <TrendingUp className="size-4" aria-hidden />
          </Link>
        ) : (
          <Logo />
        )}
      </div>
      <Tooltip label={collapsed ? "Expand the menu" : "Collapse the menu"} side="right" delay={60}>
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand the menu" : "Collapse the menu"}
          className="absolute top-6 -right-3 z-10 grid size-6 cursor-pointer place-items-center rounded-full border border-border bg-surface text-muted shadow-sm transition-colors hover:text-foreground"
        >
          {collapsed ? (
            <ChevronRight className="size-3.5" aria-hidden />
          ) : (
            <ChevronLeft className="size-3.5" aria-hidden />
          )}
        </button>
      </Tooltip>
      <nav aria-label="Main" className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {(isAdmin ? [...NAV, ADMIN_NAV] : NAV).map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          const link = (
            <Link
              key={href}
              href={href}
              aria-label={collapsed ? label : undefined}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center rounded-lg py-2 text-sm transition-colors",
                collapsed ? "justify-center px-0" : "gap-3 px-3",
                active
                  ? "bg-primary text-primary-foreground"
                  : "text-muted hover:bg-surface-2 hover:text-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {!collapsed && <span className="truncate">{label}</span>}
            </Link>
          );
          return collapsed ? (
            <Tooltip key={href} label={label} side="right" delay={60}>
              {link}
            </Tooltip>
          ) : (
            link
          );
        })}
      </nav>
      <div className="mt-2 border-t border-border pt-3">{!collapsed && <UserCard />}</div>
      {!collapsed && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the menu"
          onPointerDown={drag}
          onDoubleClick={() => setWidth(DEFAULT)}
          className="absolute inset-y-0 -right-1 z-0 w-2 cursor-col-resize hover:bg-primary/30"
        />
      )}
    </aside>
  );
}
