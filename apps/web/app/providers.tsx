"use client";

import { ConfirmProvider } from "@algoearning/ui";
import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

/** Light / dark / system theme via a class on <html>; the choice is remembered per browser. Dark until chosen,
 * like the landing page. */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      <ConfirmProvider>{children}</ConfirmProvider>
    </ThemeProvider>
  );
}
