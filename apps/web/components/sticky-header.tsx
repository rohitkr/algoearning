"use client";

import { cn } from "@algoearning/ui";
import { useEffect, useState, type ReactNode } from "react";

const HEIGHT = 56; // h-14

/** The top bar: it scrolls away with the page like any other content, and once you are further down it comes
 * back, sliding in from the top and staying fixed. A place-holder keeps the page from jumping. */
export function StickyHeader({ children }: { children: ReactNode }) {
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const onScroll = () => setStuck((s) => (s ? window.scrollY > 4 : window.scrollY > HEIGHT * 2));
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    // pages (the strategy builder) move their main action into the header while it is fixed
    document.documentElement.toggleAttribute("data-header-stuck", stuck);
    window.dispatchEvent(new Event("header-stuck"));
    return () => document.documentElement.removeAttribute("data-header-stuck");
  }, [stuck]);
  return (
    <div className="h-14">
      <header
        className={cn(
          "flex h-14 items-center justify-end gap-2 border-b border-border bg-surface px-4",
          stuck &&
            "animate-header-in fixed top-0 right-0 left-0 z-40 shadow-md md:left-[var(--sidebar-w,15rem)]",
        )}
      >
        {children}
      </header>
    </div>
  );
}
