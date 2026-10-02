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
    <div className="mx-auto flex max-w-[1800px] flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Charts</h1>
        <p className="text-sm text-muted">
          Live index candles with Smart Money Concepts: order blocks, fair value gaps, breaks of structure,
          changes of character, liquidity and key levels. Zones are recalculated every time a candle closes.
        </p>
      </div>
      {options.ok ? (
        <ChartBoard options={options.data} />
      ) : (
        <StatusPill tone="danger">Could not load the charts: {options.error.message}</StatusPill>
      )}
    </div>
  );
}
