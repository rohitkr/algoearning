# AlgoEarning SaaS

Multi-user algo-trading SaaS (Algorooms / Sensibull-style). Repo → github.com/rohitkr/algoearning.

- Monorepo: Next.js 16 web (`apps/web`), FastAPI (`apps/api`), Postgres 17 with row-level security, Redis,
  Clerk auth, Razorpay prepaid plans (test mode), Zerodha broker accounts with envelope-encrypted credentials.
- Start locally with `make dev`; `make check` must stay green.
- Built in phases with my approval per phase: done P1–P7 (setup, DB, auth, plans, payments, brokers).
  Next: P8 strategy builder, P9 multi-user trading engine (ported from `~/git/algo-trading-claude`),
  P10 central Breeze market-data feed.
- Architecture decisions live in `docs/adr/`. Dark + light theme on every screen.
