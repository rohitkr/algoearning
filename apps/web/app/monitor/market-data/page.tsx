import type { MarketDataAdmin } from "@algoearning/api-types";

import { LoadError } from "@/components/monitor/bits";
import { MarketDataPanel } from "@/components/monitor/market-data-admin";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Market data" };

export default async function MonitorMarketData({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const r = await apiGet<MarketDataAdmin>("/v1/admin/market-data");
  if (!r.ok) return <LoadError what="market data" message={r.error.message} />;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Market data</h1>
        <p className="text-sm text-muted">
          One price feed for the whole platform: every user&apos;s strategies and screens read from it.
        </p>
      </div>
      <MarketDataPanel data={r.data} redirectToken={sp.apisession} />
    </div>
  );
}
