import type { ChartOptions } from "@algoearning/api-types";
import { StatusPill } from "@algoearning/ui";

import { ChartBoard } from "@/components/charts/chart-board";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Charts" };

export default async function ChartsPage() {
  await requireUser();
  const options = await apiGet<ChartOptions>("/v1/charts/options");
  return (
    <div className="mx-auto flex max-w-[1800px] flex-col gap-3">
      <h1 className="sr-only">Charts</h1>
      {options.ok ? (
        <ChartBoard options={options.data} />
      ) : (
        <StatusPill tone="danger">Could not load the charts: {options.error.message}</StatusPill>
      )}
    </div>
  );
}
