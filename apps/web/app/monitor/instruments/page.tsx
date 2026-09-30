import type { InstrumentAdmin } from "@algoearning/api-types";
import { Card } from "@algoearning/ui";

import { LoadError } from "@/components/monitor/bits";
import { InstrumentsAdmin } from "@/components/monitor/instruments-admin";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Instruments" };

export default async function MonitorInstruments() {
  const r = await apiGet<InstrumentAdmin[]>("/v1/admin/instruments");
  if (!r.ok) return <LoadError what="instruments" message={r.error.message} />;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Instruments</h1>
        <p className="text-sm text-muted">
          Lot size, strike step and expiry type refresh from Zerodha&apos;s instrument list every day at
          08:00. Trading hours and availability are yours to set.
        </p>
      </div>
      <Card>
        <InstrumentsAdmin instruments={r.data} />
      </Card>
    </div>
  );
}
