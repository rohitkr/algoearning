# 0006 Market data: one platform feed; brokers execute only

**Decision.** Prices come from ONE platform-owned feed behind a `MarketDataProvider` interface: ICICI Breeze
first (its streaming WebSocket for live ticks, REST for history), a licensed paid vendor later (config
change). Ticks go to Redis and are fanned out to every user's UI and engine. Users' broker APIs are used for
execution only (login, orders, order book, positions, margins), so users need no paid data plan.

**To verify (phase 10).** Instruments per Breeze stream; Breeze coverage of MCX. The daily Breeze login is an
admin task.
