"use client";

import type { components } from "@algoearning/api-types";
import { Button } from "@algoearning/ui";
import { useAuth } from "@clerk/nextjs";
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRequestError, apiPost } from "@/lib/client-api";

type Checkout = components["schemas"]["CheckoutOut"];
type Purchase = components["schemas"]["PurchaseOut"];

interface RazorpaySuccess {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}
interface RazorpayInstance {
  open(): void;
  on(event: "payment.failed", cb: (r: { error: { description?: string } }) => void): void;
}
declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance;
  }
}

const CHECKOUT_JS = "https://checkout.razorpay.com/v1/checkout.js";
const dateFmt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium" });

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_JS;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("could not load Razorpay Checkout"));
    document.body.appendChild(s);
  });
}

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Buy one period of a plan: our API creates the order (it sets the price), Razorpay Checkout collects the
 * payment, then our API verifies it with Razorpay before extending the plan. */
export function BuyPlanButton({
  planCode,
  label,
  variant = "primary",
}: {
  planCode: string;
  label: string;
  variant?: "primary" | "secondary";
}) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  async function buy() {
    setBusy(true);
    setMessage(null);
    try {
      const [co] = await Promise.all([
        apiPost<Checkout>("/v1/billing/checkout", { plan_code: planCode }, getToken),
        loadCheckout(),
      ]);
      if (!window.Razorpay) throw new Error("Razorpay Checkout is unavailable");
      const rzp = new window.Razorpay({
        key: co.key_id,
        order_id: co.order_id,
        amount: co.amount_paise,
        currency: co.currency,
        name: "AlgoEarning",
        description: `${co.plan_name}, until ${dateFmt.format(new Date(co.period_end))}`,
        prefill: { email: co.customer_email, name: co.customer_name ?? undefined },
        theme: { color: cssVar("--primary", "#2459e0") },
        modal: { ondismiss: () => setBusy(false) },
        handler: async (r: RazorpaySuccess) => {
          try {
            const res = await apiPost<Purchase>(
              "/v1/billing/verify",
              {
                order_id: r.razorpay_order_id,
                payment_id: r.razorpay_payment_id,
                signature: r.razorpay_signature,
              },
              getToken,
            );
            setMessage({
              tone: "ok",
              text: `Payment received: ${co.plan_name} is active${res.period_end ? ` until ${dateFmt.format(new Date(res.period_end))}` : ""}.`,
            });
            router.refresh();
          } catch (e) {
            setMessage({
              tone: "err",
              text: `Payment received but not confirmed yet (${(e as Error).message}). It will be applied automatically; refresh in a minute.`,
            });
          } finally {
            setBusy(false);
          }
        },
      });
      rzp.on("payment.failed", (r) =>
        setMessage({ tone: "err", text: r.error.description ?? "Payment failed." }),
      );
      rzp.open();
    } catch (e) {
      const text =
        e instanceof ApiRequestError ? e.message : `Could not start the payment: ${(e as Error).message}`;
      setMessage({ tone: "err", text });
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button className="w-full" variant={variant} onClick={buy} disabled={busy}>
        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
        {label}
      </Button>
      {message && (
        <p role="status" className={message.tone === "ok" ? "text-sm text-profit" : "text-sm text-loss"}>
          {message.text}
        </p>
      )}
    </div>
  );
}
