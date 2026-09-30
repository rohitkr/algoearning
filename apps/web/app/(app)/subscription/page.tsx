import type { Entitlements, Plan, components } from "@algoearning/api-types";
import { formatInr } from "@algoearning/shared";
import { Button, Card, CardTitle, Meter, StatusPill, cn } from "@algoearning/ui";
import { Check, X } from "lucide-react";

import { BuyPlanButton } from "@/components/buy-plan-button";
import { apiGet } from "@/lib/api";
import { requireUser } from "@/lib/session";

export const metadata = { title: "Subscription" };

const USAGE_LABELS: Record<string, string> = {
  max_strategies: "Saved strategies",
  max_running_strategies: "Running strategies",
  max_broker_accounts: "Broker accounts",
};
const STATUS_TONE = { active: "success", cancelled: "warning", past_due: "danger" } as const;
const date = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

function FeatureValue({
  kind,
  value,
}: {
  kind: "flag" | "limit";
  value: boolean | number | null | undefined;
}) {
  if (kind === "flag")
    return value ? (
      <Check className="size-4 text-profit" aria-label="Included" />
    ) : (
      <X className="size-4 text-muted" aria-label="Not included" />
    );
  return <span className="tabular-nums">{value == null ? "Unlimited" : String(value)}</span>;
}

export default async function SubscriptionPage() {
  await requireUser();
  const [ent, plans, payments] = await Promise.all([
    apiGet<Entitlements>("/v1/me/entitlements"),
    apiGet<Plan[]>("/v1/plans"),
    apiGet<components["schemas"]["PaymentOut"][]>("/v1/billing/payments"),
  ]);
  if (!ent.ok || !plans.ok) {
    const err = !ent.ok ? ent.error : !plans.ok ? plans.error : null;
    return <StatusPill tone="danger">Could not load your plan: {err?.message}</StatusPill>;
  }
  const e = ent.data;
  const currentPrice = plans.data.find((x) => x.code === e.plan_code)?.price_paise ?? 0;
  const status = e.subscription_status as keyof typeof STATUS_TONE | null;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <h1 className="text-2xl font-semibold">Subscription</h1>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle>Current plan</CardTitle>
          <div className="mt-2 flex items-center gap-3">
            <p className="text-2xl font-semibold">{e.plan_name}</p>
            {status ? (
              <StatusPill tone={STATUS_TONE[status] ?? "neutral"}>{status.replace("_", " ")}</StatusPill>
            ) : (
              <StatusPill>Free forever</StatusPill>
            )}
          </div>
          <p className="mt-1 text-sm text-muted">
            {e.current_period_end
              ? `${status === "cancelled" ? "Ends" : "Renews"} on ${date.format(new Date(e.current_period_end))}`
              : e.features.live_trading
                ? "Live trading enabled"
                : "Paper trading only: upgrade to trade live on your broker account"}
          </p>
        </Card>
        <Card className="flex flex-col gap-4">
          <CardTitle>Usage</CardTitle>
          {Object.entries(e.usage).map(([key, u]) => (
            <Meter key={key} label={USAGE_LABELS[key] ?? key} used={u.used} limit={u.limit ?? null} />
          ))}
        </Card>
      </div>

      <section aria-labelledby="plans-heading" className="flex flex-col gap-4">
        <h2 id="plans-heading" className="text-lg font-semibold">
          Plans
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {plans.data.map((p) => {
            const current = p.code === e.plan_code;
            return (
              <Card
                key={p.code}
                className={cn("flex flex-col", current && "border-primary ring-1 ring-primary")}
              >
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">{p.name}</h3>
                  {current && <StatusPill tone="info">Current</StatusPill>}
                </div>
                <p className="mt-2 text-3xl font-semibold tabular-nums">
                  {p.price_paise === 0 ? "Free" : formatInr(p.price_paise / 100).replace(/\.00$/, "")}
                  {p.price_paise > 0 && (
                    <span className="text-sm font-normal text-muted"> / {p.interval}</span>
                  )}
                </p>
                <ul className="mt-4 flex flex-1 flex-col gap-2 text-sm">
                  {e.catalog.map((f) => (
                    <li key={f.key} className="flex items-center justify-between gap-3">
                      <span className="text-muted">{f.label}</span>
                      <FeatureValue
                        kind={f.kind}
                        value={p.features[f.key] as boolean | number | null | undefined}
                      />
                    </li>
                  ))}
                </ul>
                <div className="mt-5">
                  {p.price_paise === 0 ? (
                    <Button className="w-full" variant="secondary" disabled>
                      {current ? "Your plan" : "Included for everyone"}
                    </Button>
                  ) : (
                    <BuyPlanButton
                      planCode={p.code}
                      variant={current ? "secondary" : "primary"}
                      label={
                        current
                          ? `Extend by 1 ${p.interval}`
                          : `${p.price_paise > currentPrice ? "Upgrade to" : "Switch to"} ${p.name}`
                      }
                    />
                  )}
                </div>
              </Card>
            );
          })}
        </div>
        <p className="text-xs text-muted">
          Plans are prepaid: you pay for one period at a time and can extend any time. Switching plans credits
          the unused value of your current plan as extra time on the new one. Test mode: no real money is
          charged.
        </p>
      </section>

      <section aria-labelledby="history-heading" className="flex flex-col gap-3">
        <h2 id="history-heading" className="text-lg font-semibold">
          Payment history
        </h2>
        <Card className="overflow-x-auto p-0">
          {payments.ok && payments.data.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-muted">
                <tr className="border-b border-border">
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Plan</th>
                  <th className="px-5 py-3 font-medium">Amount</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Payment ID</th>
                </tr>
              </thead>
              <tbody>
                {payments.data.map((h) => (
                  <tr key={h.order_id} className="border-b border-border last:border-0">
                    <td className="px-5 py-3">{date.format(new Date(h.paid_at ?? h.created_at))}</td>
                    <td className="px-5 py-3">{h.plan_name}</td>
                    <td className="px-5 py-3 tabular-nums">{formatInr(h.amount_paise / 100)}</td>
                    <td className="px-5 py-3">
                      <StatusPill tone={h.status === "paid" ? "success" : "danger"}>{h.status}</StatusPill>
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-muted">{h.payment_id ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="px-5 py-4 text-sm text-muted">No payments yet.</p>
          )}
        </Card>
      </section>
    </div>
  );
}
