# 0015 Live execution on Zerodha

Extends 0014 (the engine), which shipped paper trading only.

**Decision.** A live run's intents go to a `LiveAccount` for its broker account (`ae_engine.live`), which works in
the background so one user's order waiting never holds up another user's strategies; outcomes are applied on a
later engine step. The order path is ported from algo-trading-claude's `zerodha/` (the code its manual trader used
for real orders), async over Kite's REST API (`ae_brokers.kite`):

- **Entries:** one basket-margin check for the batch (hedge benefit included) against available funds plus a 10%
  buffer; legs in the runner's order (hedges first); each leg sliced at the exchange freeze quantity
  (`instruments.freeze_qty`, editable in Monitor), placed as a marketable limit (LTP +/- 2%, rounded to tick),
  re-priced up to 3 times, then cancelled. If a leg fails, the batch's filled legs are unwound, shorts first, so a
  short is never left without its hedge.
- **Exits:** same path; a failed exit is retried every 5 seconds until it goes through (one event per failure, not
  one per retry). Stop, stop-all, kill switch, daily limits and the platform halt all square off at the broker.
- **Never twice:** every order carries a tag derived from its position; the in-flight intents are stored on the
  run, and after a restart the engine settles them from Zerodha's order book instead of sending again.
- **Reconciliation:** every minute, each account's expected net quantity per contract is compared with Zerodha's
  positions. A position Zerodha no longer holds (closed in Kite) is closed here too after two checks in a row;
  any other difference is reported, never "fixed" automatically.
- **Sessions:** the user's daily Zerodha token (encrypted, ADR 0007) is decrypted by the engine
  (`APP_ENCRYPTION_KEY`); when it expires, entries are refused with the reason and exits keep retrying until the
  user logs in again.

**Gates for real orders** (all required): the plan's `live_trading` flag, an admin's per-user unlock
(`user_overrides.live_unlocked`, Monitor), a broker account logged in today with its Trading Engine switch on, and
the strategy's name typed to confirm. A red LIVE banner shows while real orders can be placed.

**Dry run:** a live run with `dry_run` works out every order and logs it ("would SELL 130 NIFTY...") without any
broker call, and needs neither an unlock nor a login. Its trades are recorded as paper, so reports never mix them
with real money.

**To verify before real money:** Kite's basket-margin and order endpoints against a real account (tests use a fake
Kite), current freeze quantities, and the static-IP registration SEBI requires for algo orders (hosting phase).
