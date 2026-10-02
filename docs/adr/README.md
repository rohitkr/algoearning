# Architecture decision records

Short records of decisions that shape the codebase. Change a decision by adding a new record that
supersedes the old one, never by editing history.

| #    | Decision                                                                                  | Status                       |
| ---- | ----------------------------------------------------------------------------------------- | ---------------------------- |
| 0001 | [Monorepo: pnpm + Turborepo, uv workspace](0001-monorepo.md)                              | Accepted                     |
| 0002 | [Frontend: Next.js, Tailwind, light + dark themes](0002-frontend.md)                      | Accepted                     |
| 0003 | [Backend: Python FastAPI, reuse the proven trading engine](0003-backend.md)               | Accepted                     |
| 0004 | [Auth: Clerk behind an adapter](0004-auth.md)                                             | Accepted                     |
| 0005 | [Data: PostgreSQL + Redis](0005-data.md)                                                  | Accepted                     |
| 0006 | [Market data: one platform feed; brokers execute only](0006-market-data.md)               | Accepted                     |
| 0007 | [Brokers: multi-account, BrokerAdapter, encrypted secrets](0007-brokers.md)               | Accepted                     |
| 0008 | [Payments: Razorpay Subscriptions](0008-payments.md)                                      | Accepted                     |
| 0009 | [Hosting: portable Docker, provider decided later](0009-hosting.md)                       | Accepted                     |
| 0010 | [Strategy configs: one typed, versioned schema](0010-strategy-configs.md)                 | Accepted (instruments: 0011) |
| 0011 | [Instruments and trading hours are data, refreshed daily](0011-instruments-as-data.md)    | Accepted                     |
| 0012 | [Monitor: the admin panel, and per-user limits](0012-monitor.md)                          | Accepted                     |
| 0013 | [The platform price feed: Breeze streaming through Redis](0013-price-feed.md)             | Accepted                     |
| 0014 | [The trading engine: one process, isolated runs, paper first](0014-trading-engine.md)     | Accepted                     |
| 0015 | [Live execution on Zerodha](0015-live-execution.md)                                       | Accepted                     |
| 0016 | [Notifications: an outbox, email and Telegram, chosen per user](0016-notifications.md)    | Accepted                     |
| 0017 | [Backtesting: the live runners, replayed over stored history](0017-backtesting.md)        | Accepted                     |
| 0018 | [SMC options scalping: objective rules, options bought](0018-smc-options-scalping.md)     | Proposed                     |
| 0019 | [Home hosting: this Mac behind a Cloudflare Tunnel](0019-home-hosting.md)                 | Accepted                     |
| 0020 | [Live SMC charts: feed candles, `smartmoneyconcepts` zones, SSE](0020-live-smc-charts.md) | Accepted                     |
| 0021 | [Second price provider: the platform Kite, Breeze default](0021-kite-price-provider.md)   | Accepted                     |
