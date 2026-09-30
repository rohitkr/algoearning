import { ThemeToggle } from "@algoearning/ui";
import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "./logo";

/** Centered card layout for Clerk's sign-in / sign-up screens. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-4">
        <Link href="/" aria-label="AlgoEarning home">
          <Logo />
        </Link>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 py-8 md:items-center">{children}</main>
    </div>
  );
}
