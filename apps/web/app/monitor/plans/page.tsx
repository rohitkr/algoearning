import type { Entitlements, PlanAdmin } from "@algoearning/api-types";

import { LoadError } from "@/components/monitor/bits";
import { PlanEditor } from "@/components/monitor/plan-editor";
import { apiGet } from "@/lib/api";

export const metadata = { title: "Plans" };

export default async function MonitorPlans() {
  const [plans, ent] = await Promise.all([
    apiGet<PlanAdmin[]>("/v1/admin/plans"),
    apiGet<Entitlements>("/v1/me/entitlements"), // for the feature catalogue (labels and kinds)
  ]);
  if (!plans.ok || !ent.ok)
    return (
      <LoadError
        what="plans"
        message={(!plans.ok ? plans.error : !ent.ok ? ent.error : null)?.message ?? ""}
      />
    );
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold">Plans</h1>
        <p className="text-sm text-muted">
          Prices and limits apply to every user on the plan at once. For a single user, set custom limits on
          their page instead.
        </p>
      </div>
      {plans.data.map((p) => (
        <PlanEditor key={p.code} plan={p} catalog={ent.data.catalog} />
      ))}
    </div>
  );
}
