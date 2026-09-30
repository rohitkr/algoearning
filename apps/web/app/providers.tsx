"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

/** Light / dark / system theme via a class on <html>; the choice is remembered per browser. */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </ThemeProvider>
  );
}
