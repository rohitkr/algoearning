# 0012 Monitor: the admin panel, and per-user limits

**Decision.** The admin panel ("Monitor") is part of the web app at `/monitor`, and in production also answers on
its own host, `monitor.<domain>` (the proxy serves the panel at that host's root; one deployment, one sign-in).
It shows to admins only: non-admins get a 404, and every `/v1/admin/*` call checks the admin role again.

- **Users:** search and filter, plan and usage, suspend or reactivate, make or remove admins (never your own
  account), grant a plan for N days without payment (`provider = manual`, can be ended early), and custom limits.
- **Custom limits** (`user_overrides`, one row per user): single features set on top of the plan, e.g. a tester
  with 5 broker accounts on the free plan. `ae_core.entitlements.effective_features` merges them, so the API and
  the engine enforce the same numbers. Users can read their own row (row-level security, SELECT only) but never
  write it.
- **Plans:** prices, availability and feature limits for everyone on a plan.
- **Instruments:** lot sizes and their last refresh, trading hours, availability, refresh now (ADR 0011).
- **Audit log:** every admin change is recorded with `actor = admin`, before and after values.

**Sign-in rule found on the way.** A new login whose email already belongs to an account is refused with 409, never
linked automatically: linking by email alone would let whoever controls a new login take over the old account.

**To verify at deployment (phase 15).** Clerk sessions shared between the main host and `monitor.` (same root
domain), and whether Monitor should additionally sit behind an IP allow-list.
