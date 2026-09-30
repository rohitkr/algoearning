# 0011 Instruments and trading hours are data, refreshed daily

Supersedes the "platform vs user data" paragraph of 0010, which kept the instrument catalogue in code.

**Context.** SEBI and the exchanges change lot sizes (NIFTY 75 -> 65), expiry patterns (weekly -> monthly) and
trading hours (F&O now trades until 15:40 with the closing session). None of these should need a release.

**Decision.** A platform table `instruments` (read-only for users, written by the system; seeded by migration 0005)
holds, per underlying: lot size, strike step, weekly or monthly expiries, trading hours and whether it is active.
`ae_core.strategy.check()` takes the instruments as a parameter, so the API validates, and the engine will trade,
against today's values.

- **Exchange facts** are refreshed by the worker every day at 08:00 IST (`python -m ae_worker`, also run once by
  `make dev`) from Zerodha's public instrument list: the nearest listed option expiry gives the lot size, the
  finest strike spacing and the expiry pattern. An underlying missing from the list keeps its values and is
  logged; changes are logged as warnings. When a lot size changes, new contracts carry the new size while running
  ones keep theirs until expiry, so the engine (phase 9) takes each contract's own lot size when it places orders.
- **Trading hours and availability** are admin settings (not in the broker list): 09:15-15:40 for NFO and BFO.

**Why it is safe.** Strategies store lots, never quantities: a lot-size change changes the quantity of every
strategy automatically, and the builder shows the current quantity. The set of supported underlyings stays in code
(`Underlying`), because the engine must be able to trade each one.
