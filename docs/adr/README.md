# Architecture decision records

Short records of decisions that shape the codebase. Change a decision by adding a new record that
supersedes the old one, never by editing history.

| #    | Decision                                                                               | Status                       |
| ---- | -------------------------------------------------------------------------------------- | ---------------------------- |
| 0001 | [Monorepo: pnpm + Turborepo, uv workspace](0001-monorepo.md)                           | Accepted                     |
| 0002 | [Frontend: Next.js, Tailwind, light + dark themes](0002-frontend.md)                   | Accepted                     |
| 0003 | [Backend: Python FastAPI, reuse the proven trading engine](0003-backend.md)            | Accepted                     |
| 0004 | [Auth: Clerk behind an adapter](0004-auth.md)                                          | Accepted                     |
| 0005 | [Data: PostgreSQL + Redis](0005-data.md)                                               | Accepted                     |
| 0006 | [Market data: one platform feed; brokers execute only](0006-market-data.md)            | Accepted                     |
| 0007 | [Brokers: multi-account, BrokerAdapter, encrypted secrets](0007-brokers.md)            | Accepted                     |
| 0008 | [Payments: Razorpay Subscriptions](0008-payments.md)                                   | Accepted                     |
| 0009 | [Hosting: portable Docker, provider decided later](0009-hosting.md)                    | Accepted                     |
| 0010 | [Strategy configs: one typed, versioned schema](0010-strategy-configs.md)              | Accepted (instruments: 0011) |
| 0011 | [Instruments and trading hours are data, refreshed daily](0011-instruments-as-data.md) | Accepted                     |
| 0012 | [Monitor: the admin panel, and per-user limits](0012-monitor.md)                       | Accepted                     |
| 0013 | [The platform price feed: Breeze streaming through Redis](0013-price-feed.md)          | Accepted                     |
| 0014 | [The trading engine: one process, isolated runs, paper first](0014-trading-engine.md)  | Accepted                     |
| 0015 | [Live execution on Zerodha](0015-live-execution.md)                                    | Accepted                     |
