import { Card, StatusPill } from "@algoearning/ui";
import { notFound } from "next/navigation";

import { NAV } from "@/components/nav";
import { requireUser } from "@/lib/session";

/** Placeholder for app sections not built yet, so the navigation already works end to end. */
export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  await requireUser();
  const { section } = await params;
  const item = NAV.find((n) => n.href === `/${section}`);
  if (!item) notFound();
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="text-2xl font-semibold">{item.label}</h1>
      <Card className="mt-6 flex items-center justify-between">
        <p className="text-muted">This page is built in phase {item.phase}.</p>
        <StatusPill tone="info">Coming soon</StatusPill>
      </Card>
    </div>
  );
}
