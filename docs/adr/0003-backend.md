# 0003 Backend: Python FastAPI, reuse the proven trading engine

**Context.** The local algo-trading app (Python) has working Zerodha execution, order lifecycle,
SL/target/trailing/partial rules, reconciliation, risk checks, multi-leg strategies, market-data streaming
and ~260 tests.

**Decision.** Python 3.13 + FastAPI + Pydantic for the API; a separate long-running **engine** process runs
each user's broker sessions and strategies; a **worker** runs scheduled/async jobs. Trading logic is ported
into `packages/py-core` / `py-brokers` / `py-marketdata` as per-user services, not copied as-is.

**Consequences.** No rewrite of the riskiest code; the engine can scale separately from the API.
