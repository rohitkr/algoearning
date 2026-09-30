import type { Strategy, StrategyCatalog } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";
import { notFound } from "next/navigation";

import { StrategyBuilder } from "@/components/builder/strategy-builder";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Edit strategy" };

export default async function EditStrategyPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireUser();
  const [{ id }, q] = await Promise.all([params, searchParams]);
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const [strategy, catalog] = await Promise.all([
    apiGet<Strategy>(`/v1/strategies/${id}`),
    apiGet<StrategyCatalog>("/v1/strategies/catalog"),
  ]);
  if (!strategy.ok && strategy.status === 404) notFound();
  if (!strategy.ok || !catalog.ok) {
    const err = !strategy.ok ? strategy.error : !catalog.ok ? catalog.error : null;
    return <StatusPill tone="danger">Could not load the strategy: {err?.message}</StatusPill>;
  }
  return (
    <div className="flex flex-col gap-4">
      {q.saved && (
        <div role="status" className="mx-auto w-full max-w-6xl">
          <StatusPill tone="success">Strategy saved</StatusPill>
        </div>
      )}
      <StrategyBuilder key={strategy.data.id} catalog={catalog.data} strategy={strategy.data} />
    </div>
  );
}
