import type { StrategyPage } from "@algoearning/api-types";
import { Button, Card, StatusPill, cn } from "@algoearning/ui";
import { Plus, Search } from "lucide-react";
import Link from "next/link";

import { StrategyRow } from "@/components/strategies/strategy-row";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Strategies" };

const TABS = [
  { id: "active", label: "Active", statuses: ["draft", "ready"] },
  { id: "ready", label: "Ready", statuses: ["ready"] },
  { id: "draft", label: "Drafts", statuses: ["draft"] },
  { id: "archived", label: "Archived", statuses: ["archived"] },
] as const;

export default async function StrategiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireUser();
  const sp = await searchParams;
  const tab = TABS.find((t) => t.id === sp.tab) ?? TABS[0];
  const q = (sp.q ?? "").trim();
  const qs = new URLSearchParams();
  for (const s of tab.statuses) qs.append("status", s);
  if (q) qs.set("q", q);
  if (sp.cursor) qs.set("cursor", sp.cursor);
  const page = await apiGet<StrategyPage>(`/v1/strategies?${qs}`);
  const link = (over: Record<string, string | undefined>) => {
    const p = new URLSearchParams();
    const merged = { tab: tab.id === "active" ? undefined : tab.id, q: q || undefined, ...over };
    for (const [k, v] of Object.entries(merged)) if (v) p.set(k, v);
    const s = p.toString();
    return s ? `/strategies?${s}` : "/strategies";
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Strategies</h1>
          <p className="text-sm text-muted">Your saved strategies. Only you can see them.</p>
        </div>
        <Button asChild>
          <Link href="/builder">
            <Plus className="size-4" aria-hidden /> New strategy
          </Link>
        </Button>
      </div>

      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <nav aria-label="Filter by status" className="flex flex-wrap gap-1">
            {TABS.map((t) => (
              <Link
                key={t.id}
                href={link({ tab: t.id === "active" ? undefined : t.id, cursor: undefined })}
                aria-current={t.id === tab.id ? "page" : undefined}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-sm font-medium",
                  t.id === tab.id ? "bg-accent text-primary-text" : "text-muted hover:bg-surface-2",
                )}
              >
                {t.label}
              </Link>
            ))}
          </nav>
          <form action="/strategies" className="relative ml-auto w-full sm:w-64" role="search">
            {tab.id !== "active" && <input type="hidden" name="tab" value={tab.id} />}
            <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" aria-hidden />
            <input
              type="search"
              name="q"
              defaultValue={q}
              placeholder="Search by name"
              aria-label="Search strategies by name"
              className="h-9 w-full rounded-lg border border-border bg-surface pr-3 pl-8 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </form>
        </div>

        {!page.ok ? (
          <StatusPill tone="danger">Could not load strategies: {page.error.message}</StatusPill>
        ) : page.data.items.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
            {q ? (
              <>No strategies match “{q}”.</>
            ) : tab.id === "active" ? (
              <>
                No strategies yet.{" "}
                <Link href="/builder" className="text-primary-text underline">
                  Build your first one
                </Link>{" "}
                from scratch or from a proven preset.
              </>
            ) : (
              <>Nothing here.</>
            )}
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {page.data.items.map((s) => (
              <StrategyRow key={s.id} strategy={s} />
            ))}
          </ul>
        )}

        {page.ok && (sp.cursor || page.data.next_cursor) && (
          <div className="flex justify-between text-sm">
            {sp.cursor ? (
              <Link href={link({ cursor: undefined })} className="text-primary-text underline">
                Back to first page
              </Link>
            ) : (
              <span />
            )}
            {page.data.next_cursor && (
              <Link href={link({ cursor: page.data.next_cursor })} className="text-primary-text underline">
                More
              </Link>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
