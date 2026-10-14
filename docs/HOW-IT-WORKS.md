# How AlgoEarning works: the technology, the trading flow, and interview questions

A study guide for explaining this project (a multi-user algo-trading platform for Indian index options) to an
interviewer. Part 1 is the 60-second pitch. Part 2 follows one trade from signal to exit. Part 3 is the technology
and why each piece was chosen. Part 4 is the questions you will probably be asked, with honest answers. Part 5 lists the
limits you should admit before they are found. Every number and file path below is real: open the file before the
interview so you can say "here, in `runners.py`".

The formal decision records are in [adr/](adr/README.md), the component map is in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. The pitch

> AlgoEarning is a multi-user algo-trading SaaS for Indian index options (NIFTY, BANKNIFTY, SENSEX…). A user builds a
> strategy in a no-code builder (entry time, option legs, stop-loss, target, trailing, overnight holding, even
> indicator conditions or Telegram tips), backtests it on one-minute history, runs it on paper, and then deploys it on
> their own Zerodha account. One platform price feed streams live prices into Redis; one engine process steps every
> user's strategy every second, decides entries and exits, and places orders through Zerodha's API. The same strategy
> code runs in backtests, on paper and live, so the three cannot drift apart. It has plans and payments (Razorpay),
> row-level-security data isolation, encrypted broker credentials, and an admin panel.

Scale of the code: about 31k lines of Python, 29k lines of TypeScript, 440 tracked files, 26 architecture decision
records, 170 commits.

---

## 2. One trade, end to end

Take "sell an ATM straddle at 15:20, hold overnight, exit at 13:30 next day, stop-loss 50% per leg".

1. **Build.** The Next.js builder produces a typed JSON config (`packages/py-core/src/ae_core/strategy.py`, Pydantic
   models). The API validates it (`/v1/strategies/validate`) and the UI shows a plain-English summary.
2. **Backtest.** The worker replays the same runner over stored one-minute candles (`ae_core/backtest.py`, ADR 0017).
3. **Deploy.** Creating a _run_ (`strategy_runs` table) snapshots the exact config, the multiplier (lots) and the mode:
   paper, live dry-run, or live. Live needs four gates: the plan allows it, an admin unlocked the user, the broker
   account is logged in today with its Trading Engine switch on, and the user types the strategy name to confirm.
4. **Prices.** The _feed_ process streams index prices (and any option contract someone asks for) from a market-data
   provider into Redis (`ae_marketdata`, ADR 0013).
5. **Engine step, every second.** For each active run the engine builds a `Market` (last index price, today's one-minute
   bars, option prices, lot size, expiries), calls the runner's `step(market)`, and gets back a list of _intents_
   ("SELL 1 lot NIFTY 25000 CE").
6. **Risk check on entries.** Platform halt, the user's kill switch, daily loss/profit limits, max open positions, max
   trades per day, lots per order (`ae_core/trading/risk.py`). Exits are never blocked by these checks.
7. **Execution.** Paper: fill at the last price with slippage. Live: the order goes to the user's `LiveAccount`
   (`apps/engine/src/ae_engine/live.py`): basket-margin check, hedge legs first, slice at the exchange freeze quantity,
   marketable limit order, re-price up to 3 times, unwind if a leg fails.
8. **Records.** A position becomes a `trades` row, each fill an `orders` row, each decision a `trade_events` row.
   The run's runner state (plain JSON) is saved after every step.
9. **Holding.** The state includes the computed exit time (`final`). A restart or the next day just resumes from it.
10. **Exit.** Each step, the runner checks the stop-losses, targets, trailing, whole-trade limits and finally the exit
    time. When one fires it emits exit intents, which go through the same order path. A refused exit is retried every
    5 seconds until it fills.
11. **Reconcile.** Every minute the engine compares what it thinks is open with Zerodha's positions.
12. **Report.** Realised P&L, charges, and reports for the user; notifications by email/Telegram through the worker.

---

## 3. How the important parts work

### 3.1 Where do the prices come from, and how are they watched?

- **One platform feed, not one per user** (ADR 0006/0013). A process `python -m ae_marketdata` holds _one_ websocket
  to a market-data provider (ICICI Breeze, or the platform's own Kite account; a random-walk simulator for
  development). Brokers are used for execution only. This is why 100 users do not need 100 data subscriptions or
  hit API rate limits.
- **Redis as the price bus.** The feed publishes the latest tick per key (`md:last`), the day's completed one-minute
  bars (`md:bars:<key>:<date>`), and pub/sub channels. The engine and API only read Redis (`Hub` class). Keys look like
  `NIFTY` or `NIFTY:2026-10-13:25000:CE`.
- **Only stream what is wanted.** Indices stream always. An option contract streams only while a reader has asked for
  it (`Hub.want`); interest expires after 3 minutes unless renewed. The engine asks for the contracts its open
  positions and candidate legs need.
- **Staleness guard.** The index price is ignored if older than 2 minutes (`STALE` in `engine.py`), so nothing enters on
  a dead feed. The run page shows "Prices as of …" and a warning event is written when prices stop.
- **Gap fill.** Provider sessions expire daily, and a stream only has prices from when it connected. If an admin logs
  in late, the feed notices missing minutes in today's bars and backfills them from the provider's history API.
- **History.** Each evening the day's bars are archived from Redis to Postgres (`history_candles`). That table feeds
  backtests. A Monitor > Data sync page shows missing days and downloads them.

### 3.2 How is the stop-loss monitored?

This is the question to prepare best, and the honest answer is a design trade-off.

- **The stop-loss is software-side, not a broker order.** When a leg is entered, the runner computes the stop level
  from the entry price (`on_entry` in `RulesRunner`): premium-based (e.g. entry × 1.5 for a short) or index-based.
  The level is stored on the position.
- **Every engine step (about every second)** the runner reads the current price for each open position, trails the
  stop if configured (a step ladder: every N points of favourable move the stop moves M points), and compares:
  `(price - stop) * favourable_direction <= 0` means hit. A hit emits an exit intent.
- **Whole-trade rules** are evaluated next: strategy-level stop loss and target on combined P&L, a combined-premium
  stop for sold legs, and a "lock profit" ladder (once profit reaches X, never give back below Y). They close all legs.
- **Time exit.** The exit time (including "next trading day 13:30") is evaluated before everything else each step.
- **Why not broker-side SL orders?** Trade-offs: broker SL orders survive an outage of our system, but they need
  per-leg order management, cannot express combined-premium or lock-profit rules, trailing means constant order
  modification, and they behave differently in backtests. We chose one rules engine that behaves identically in
  backtest, paper and live. The cost, which you should say out loud: **if the engine or the host machine is down, no
  stop-loss is active.** Mitigations: a heartbeat on every run with alerts, a restart resumes held trades from saved
  state, and a leader lease prevents duplicates.
- **Execution of the exit:** a marketable limit order (LTP ± 2%, rounded to tick) re-priced up to 3 times, retried every
  5 seconds if it fails. Closing shorts before their hedges keeps margin benefit.

### 3.3 One runner for backtest, paper and live

`ae_core.trading.runners` is pure Python with no database or network: a runner gets a `Market` and returns intents;
`fill()` and `reject()` tell it what happened. The engine, the paper exchange and the backtester only differ in how
they turn intents into fills. That is why backtests are trustworthy, and why the code is easy to test (hundreds of
tests, including property-style stress tests that run every plan every day and assert nothing is left open).

Backtest honesty (ADR 0017): no look-ahead (decisions at the minute's open, stops judged on that minute's adverse
extreme, targets on the favourable extreme, stop assumed first if both are reachable), stops fill at the stop level or
the open if it gapped, slippage and approximate Indian charges (brokerage, STT, exchange, SEBI, GST, stamp), and
warnings when data is incomplete.

### 3.4 Crash safety and "never send an order twice"

- Runner state is JSON saved on the run after every step, so a restart continues exactly (positions held overnight
  included).
- Orders in flight are stored on the run; each order carries a tag derived from its position id. After a restart the
  engine looks the tag up in Zerodha's order book instead of re-sending.
- A **Redis lease** (`engine:leader`, 15 s, renewed) means only one engine trades; a second copy stands by.
- Per-user isolation: each user's runs are stepped in their own transaction; an exception stops that run (status
  `error`) and never the others.
- **Reconciliation** every minute against broker positions: a position closed outside the app is closed in the app after
  two consecutive checks; any other difference is only reported, never auto-"fixed".

### 3.5 Live order execution details (ADR 0015)

Basket margin check with a 10% buffer; hedge legs first so margin benefit exists when the short is placed; slice
quantities at the exchange freeze limit; marketable LIMIT orders (never raw market orders on options); re-price up
to 3 times then cancel; if any leg fails, unwind the filled legs, shorts first, so no naked short is left. Orders
are placed by a per-account background object so one user's slow broker call never blocks other users.

### 3.6 Security and multi-tenancy

- **Auth:** Clerk handles sign-in; the API verifies the JWT against Clerk's keys and creates the user row on first
  sight. A dev-only header exists and the API refuses to start with it enabled in production.
- **Row-level security** in Postgres (ADR 0005): every user-owned table has `FORCE ROW LEVEL SECURITY` with policy
  `user_id = app_user_id()`. Requests run as role `ae_app` with the user id set per transaction. A query that forgets
  its `WHERE` still sees only that user's rows. Tests prove it, and a mutation test confirmed they fail if RLS is
  removed. Engine, worker and webhooks use a separate `ae_system` role.
- **Broker credentials:** envelope encryption. A fresh AES-256-GCM data key per value, wrapped by a master key
  (`APP_ENCRYPTION_KEY`), with the row and field name as authenticated data so ciphertext copied elsewhere will not
  decrypt. Plaintext never reaches the browser, logs or audit entries.
- **Broker login:** Zerodha's daily OAuth-style login; the redirect carries a signed, single-use, 10-minute state; the
  session is stored encrypted and expires at 06:00 IST. A login by a different Zerodha account than registered is refused.
- **Audit log** is append-only (a trigger rejects UPDATE/DELETE, even for the table owner).
- **Payments (ADR 0008):** Razorpay orders; the amount comes from the plan, never the browser. Three independent
  paths can settle a payment (verify call, signed webhook, reconcile CLI) and all go through one idempotent function
  that re-fetches the payment from Razorpay and checks order id, amount, currency and captured status.

### 3.7 Technology stack, and why

| Layer       | Choice                                                         | Why                                                                          |
| ----------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Web         | Next.js 16 (App Router), React, Tailwind, light and dark theme | server components for fast first paint, one codebase for pages and admin     |
| API         | FastAPI + Pydantic, OpenAPI → generated TypeScript types       | typed end to end: schema changes break the web build instead of production   |
| Domain      | pure Python package `py-core`                                  | no I/O, so it is deterministic and testable                                  |
| DB          | PostgreSQL 17 + SQLAlchemy 2 async + Alembic                   | transactions, RLS, NUMERIC for money                                         |
| Cache / bus | Redis                                                          | prices, pub/sub, leases, nonces                                              |
| Auth        | Clerk                                                          | not writing authentication ourselves                                         |
| Payments    | Razorpay                                                       | Indian cards/UPI                                                             |
| Market data | ICICI Breeze websocket, or Kite                                | separate from execution broker (rate limits, one subscription for all users) |
| Execution   | Zerodha Kite Connect REST                                      | most users' broker                                                           |
| Hosting     | home Mac, launchd services, Cloudflare Tunnel (ADR 0019)       | free while there are two users                                               |
| Backups     | pg_dump to Cloudflare R2, env files to Google Drive            | `docs/setup.md`                                                              |
| Monorepo    | pnpm + Turborepo (TS), uv workspace (Python)                   | shared types and one `make check`                                            |

Processes: `web`, `api`, `engine`, `feed`, `worker`, Postgres, Redis, tunnel. The worker runs scheduled jobs: queued
backtests, evening candle archive, instrument refresh, notifications, public-IP check, data sync downloads.

### 3.8 Other features you can talk about

- **Strategy kinds:** the rule builder (time-based, indicator conditions with EMA/RSI/Supertrend etc., day plans with
  different routines per weekday), range breakout, 0-DTE, and an SMC (Smart Money Concepts) scalper with pure-function
  detectors (swings, BOS/CHoCH, order blocks, fair-value gaps) that only see completed candles (ADR 0018).
- **Telegram signal source (ADR 0025):** read a tips channel through the user's own Telegram session (Telethon),
  parse messages into signals, trade the direction.
- **Monitor (admin):** feed health, market-data logins, users and plans, instruments (lot sizes and freeze quantities
  as data, not code), engine status, audit log, data sync.

---

## 4. Questions you may be asked

### Architecture

**Q: Why a separate engine process and not run strategies in the API?**
The API serves HTTP and must stay responsive; the engine is a long-running loop with a leader lease and its own
failure isolation. Separating them lets us restart the web side without touching trades.

**Q: How does it scale to many users?**
Prices: one feed, so cost does not grow with users. Engine: one process loops over runs, cheap per run (pure
functions on in-memory data); broker I/O is asynchronous per account so slow brokers do not block others. The limit
is single-process stepping and a single Redis/Postgres. Next steps would be sharding runs by user across engine
instances (the lease would become per-shard) and moving to a VPS/cloud. Admit that today it has two users.

**Q: Why Redis for prices and not Postgres?**
Ticks are high-rate, short-lived, read by several processes; Redis gives O(1) latest-value reads and pub/sub. Postgres
stores what must be durable: the evening candle archive.

**Q: Why polling once a second instead of event-driven on every tick?**
Strategies here act on minute bars and price thresholds, not microsecond latency. A one-second loop is simple,
deterministic, and the same code path as backtests. For HFT it would be wrong; for options swing/intraday rules it is
enough. Worst-case stop-loss reaction is about one second plus order latency.

**Q: What happens if the engine crashes in the middle of a trade?**
State is saved after every step. On restart it reloads the run, settles in-flight orders from the broker's order book
(by tag, no double send), and resumes managing open legs including the exit time. We saw this live: a restart resumed
four open legs and exited them.

**Q: How do you prevent duplicate orders?**
Order tags derived from position ids, in-flight intents persisted, reconciliation against the broker, and a Redis
leader lease so two engines cannot trade.

### Trading logic

**Q: How do you monitor the stop-loss? Is it at the broker?**
See 3.2. Software-side, evaluated every second on live option premiums (or the index), including trailing and
whole-trade rules; exit by marketable limit order with retries. Say the trade-off about engine downtime.

**Q: What if the exit order does not fill, or the price gaps through the stop?**
The limit is LTP ± 2% (marketable) and re-priced up to 3 times; failures retry every 5 seconds. A gap means the fill is
at the then-current price, so real loss can exceed the stop level; backtests model this by filling at the open if the
minute gapped through.

**Q: How do you handle slippage, charges, partial fills, freeze quantity?**
Slippage setting (default 0.05%) in paper and backtests; Indian charges modelled approximately; orders sliced at the
instrument's freeze quantity; partial fills reported and applied; if a leg fails the batch is unwound.

**Q: How do you avoid look-ahead bias in backtests?**
Decisions at a minute's open using only completed candles; higher-timeframe candles usable only after their last
minute closes; stop/target judged on adverse/favourable extremes with stop-first when ambiguous. Honest limits: one-minute
data cannot resolve intra-minute order, and historical lot sizes are not modelled (it uses today's).

**Q: How do you test trading code?**
Unit tests on the pure runners with a simulated market; scenario tests for overnight holds, restarts and missed exits;
stress tests across seeds; engine tests with a fake Kite; row-level-security tests; a mutation check that the
isolation tests fail when RLS is removed.

**Q: Tell me about a bug you hit in production.**
Good true story: a live overnight trade did not exit at 13:30. Cause: live orders fill a few seconds after the step
that sent them. In those seconds the strategy saw no open positions and wrongly marked the trade finished, so later
fills arrived on a trade counted as over, and the exit, stop-losses and limits never ran. Fix: entries still on their way
count as part of the open trade until they fill or are refused, a fill always puts the trade back under management, and a
runner that finds open legs on a "finished" trade resumes them. A follow-up stops a never-answered order from holding a
trade open into the next day. Lessons: paper trading fills instantly, so it hid a live-only race; add tests for the
async gap; monitor for "open positions but trade marked over".

### Data, security, compliance

**Q: How is user data isolated?** See 3.6: RLS enforced by the database, tested.

**Q: How do you store broker API keys?** Envelope encryption AES-256-GCM, master key in the environment (KMS planned).

**Q: SEBI / regulatory concerns?**
Retail algo orders need registered static IPs per Kite app (we show and monitor the server's IP and alert on change);
brokers' API terms, and advisory vs execution distinction: the platform executes users' own rules on their own accounts and
does not give tips. Be careful not to claim compliance you have not verified.

**Q: How do payments avoid double-crediting or tampering?** Amount from the server-side plan, idempotent settlement
from three paths, re-fetch from Razorpay, unique payment ids.

### Frontend / product

**Q: How does the UI stay in sync with the backend types?** FastAPI OpenAPI → generated TypeScript types in CI; a
mismatch fails the build.

**Q: What are the hard UI problems?** Showing a strategy in plain English from config, validating while typing
(debounced server validation), live prices and P&L without flooding, dark/light theme across every screen, mobile layout.

### Operations

**Q: How is it deployed and backed up?** Home Mac with launchd services behind a Cloudflare Tunnel; database dumps to
Cloudflare R2 and env files to Google Drive; a documented restore on a new machine. Admit it is a single point of failure.

**Q: What would you change with more time or money?** VPS with a static IP, Postgres backups on a schedule, a second
engine host with per-shard leases, broker-side protective orders as a safety net under the software stop-loss,
alerting (pager) on heartbeat loss, load tests, and a proper staging environment with a paper-broker simulator.

### Honest "what are you proud of / what did you learn"

Proud of: one runner for backtest/paper/live; crash-safe state; database-enforced isolation; a real live incident debugged
and fixed. Learned: distributed-systems failure modes show up only live (async fills, expired sessions, stale feeds);
write the invariants down ("an open position always has a managing trade") and test them.

---

## 5. Limits to admit before they are found

- Single host (a Mac), so a sleeping, offline or crashed machine means no stop-loss monitoring. No broker-side stop orders.
- Few users (the app is used by the founder and a friend); scaling numbers are design intent, not load-tested.
- Prices come from one provider session that expires daily and needs a manual login; a missed login means no prices.
- Zerodha sessions also expire daily around 06:00; an overnight trade's exit depends on logging in that morning.
- Backtests use one-minute data (no tick-level path) and today's lot sizes; results are estimates, not promises.
- Several integrations were verified only against test doubles and then against live use incrementally (Breeze socket
  details, Kite order endpoints); say "verified live for X, Y" only for what you actually ran.
- Not a registered investment advisor product; do not describe returns or promise profits.

## 6. Ten-minute demo script

1. Dashboard: running strategy, live P&L, LIVE banner.
2. Builder: create a straddle; show the plain-English summary and validation.
3. Backtest it; show daily P&L, trades, warnings about data gaps.
4. Run page: events log (entry, trailing moved, exit reason).
5. Monitor: feed health, market-data login, data sync calendar with holidays.
6. Open `runners.py` `_step`: the exit-time check, then leg stops, then whole-trade limits. Open `live.py`: marketable
   limit and re-pricing. Open `rls.py`: the policy.
