import type { StrategyCatalog } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

import { StrategyBuilder } from "@/components/builder/strategy-builder";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Strategy Builder" };

export default async function NewStrategyPage() {
  await requireUser();
  const catalog = await apiGet<StrategyCatalog>("/v1/strategies/catalog");
  if (!catalog.ok)
    return <StatusPill tone="danger">Could not load the builder: {catalog.error.message}</StatusPill>;
  return <StrategyBuilder catalog={catalog.data} />;
}
