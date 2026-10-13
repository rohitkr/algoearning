"use client";

import { cn } from "@algoearning/ui";
import { useEffect, useRef, useState, type ReactNode } from "react";

/** A top bar that scrolls away with the page like any other content and, the moment it is out of view, comes back
 * sliding in from the top and stays fixed. A place-holder keeps the page from jumping. `className` styles the bar,
 * `fixedClassName` where it sits while fixed (e.g. beside the side menu). */
export function StickyHeader({
  children,
  className,
  fixedClassName = "left-0",
}: {
  children: ReactNode;
  className?: string;
  fixedClassName?: string;
}) {
  const [stuck, setStuck] = useState(false);
  const bar = useRef<HTMLElement>(null);
  const [height, setHeight] = useState<number | undefined>();
  useEffect(() => {
    const onScroll = () => {
      const h = bar.current && !bar.current.classList.contains("fixed") ? bar.current.offsetHeight : 0;
      if (h) setHeight(h);
      // fixed again as soon as the whole bar has left the screen; back in place at the very top
      setStuck((s) => (s ? window.scrollY > 4 : window.scrollY > (h || 56)));
    };
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
    <div style={{ height }} className={height ? undefined : "h-14"}>
      <header
        ref={bar}
        className={cn(
          className,
          stuck && cn("animate-header-in fixed top-0 right-0 z-40 shadow-md", fixedClassName),
        )}
      >
        {children}
      </header>
    </div>
  );
}
