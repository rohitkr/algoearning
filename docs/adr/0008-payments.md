# 0008 Payments: Razorpay, prepaid plans first

**Decision.** Plans are sold as **prepaid periods** (one month / one year at a time) through Razorpay Orders +
Standard Checkout (card, UPI, net banking). Auto-renewing Razorpay Subscriptions can be added later behind the same
billing service (`subscriptions.kind = prepaid | recurring`); it needs the Subscriptions product enabled on the
Razorpay account, and live payments need the account's SEBI-related activation documents.

**Flow.** `POST /v1/billing/checkout` creates a Razorpay order whose amount comes from the plan (never from the
browser) -> Razorpay Checkout -> `POST /v1/billing/verify`. Three independent paths can settle a payment, in any
order, any number of times: the verify call, the signed webhook `/v1/webhooks/razorpay`, and
`python -m ae_api.cli reconcile-payments`. All go through `billing.service.settle()`, which locks the order row,
fetches the payment from Razorpay server-side, requires the same order id, exact amount and currency and a captured
status (capturing an authorized payment), and applies it once (a paid order is never applied again; payment ids
are unique).

**Periods** (`ae_core.billing`): month = 30 days, year = 365. Renewing the same plan extends from its end;
switching plans ends the old one now and credits its unused value as extra time on the new plan.
