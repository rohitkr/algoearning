"use client";

import { cn } from "@algoearning/ui";
import { useUser } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";

/** Sidebar footer: who is signed in; links to the profile page. */
export function UserCard() {
  const { user, isLoaded } = useUser();
  const pathname = usePathname();
  const active = pathname.startsWith("/profile");
  if (!isLoaded || !user) return <div className="h-12" aria-hidden />;
  const name = user.fullName || user.primaryEmailAddress?.emailAddress || "Account";
  return (
    <Link
      href="/profile"
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-surface-2",
        active && "bg-surface-2",
      )}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- Clerk-hosted avatar, tiny, no optimisation needed */}
      <img src={user.imageUrl} alt="" className="size-9 rounded-full border border-border" />
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{name}</span>
        <span className="block truncate text-xs text-muted">{user.primaryEmailAddress?.emailAddress}</span>
      </span>
    </Link>
  );
}
