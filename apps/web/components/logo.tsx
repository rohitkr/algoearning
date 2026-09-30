import { TrendingUp } from "lucide-react";

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2 text-lg font-semibold tracking-tight">
      <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
        <TrendingUp className="size-4" aria-hidden />
      </span>
      AlgoEarning
    </span>
  );
}
