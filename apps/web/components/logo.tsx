import { TrendingUp } from "lucide-react";
import Link from "next/link";

/** The brand mark and name. Clicking it goes to the dashboard from anywhere (sign-in still applies there). */
export function Logo({ href = "/dashboard" }: { href?: string }) {
  return (
    <Link
      href={href}
      aria-label="AlgoEarning: go to the dashboard"
      className="inline-flex items-center gap-2 rounded-lg text-lg font-semibold tracking-tight"
    >
      <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
        <TrendingUp className="size-4" aria-hidden />
      </span>
      AlgoEarning
    </Link>
  );
}
