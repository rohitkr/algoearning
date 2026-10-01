import { ThemeToggle } from "@algoearning/ui";
import type { ReactNode } from "react";

import { Logo } from "./logo";

/** Centered card layout for Clerk's sign-in / sign-up screens. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-4">
        <Logo href="/" />
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 py-8 md:items-center">{children}</main>
    </div>
  );
}
