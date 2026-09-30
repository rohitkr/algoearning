import { cn } from "../cn";

/** Usage against a limit, e.g. "3 of 5 strategies". limit null = unlimited (no bar). Warns at 80%, full at 100%. */
export function Meter({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const pct = limit == null ? 0 : limit === 0 ? 100 : Math.min(100, Math.round((used / limit) * 100));
  const tone = limit == null ? "" : pct >= 100 ? "bg-loss" : pct >= 80 ? "bg-warning" : "bg-primary";
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-muted">
          {used} / {limit == null ? "Unlimited" : limit}
        </span>
      </div>
      <div
        className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-2"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit ?? used}
        aria-valuenow={used}
      >
        {limit != null && <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}
