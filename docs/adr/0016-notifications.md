# 0016 Notifications: an outbox, email and Telegram, chosen per user

**Decision.** The engine never sends anything itself: when something a user may care about happens (an entry or
exit, a stop-loss or target, an order that failed or is being retried, a risk limit, an expired broker login, a run
that stopped or failed, a reconciliation difference) it adds a row to `notifications` (user-owned, RLS) in the same
transaction as the event, so a notification exists exactly when the event does. The worker sends them:

- Every 5 seconds it takes the pending rows and applies the user's `notification_settings`: events they switched
  on (defaults when they never chose; per-trade alerts are off by default), then their channels. Email goes over
  SMTP (`SMTP_HOST` etc.; to the address they set, else their account email), Telegram over the Bot API.
- A row ends `sent` (with the channels used), `skipped` (event or channels off), or `failed` after 3 attempts; the
  user sees this history and each error on the Notifications page. A failing channel never blocks the other.
- **Telegram linking:** the user presses Connect, gets a one-time code, opens `t.me/<bot>?start=<code>`; the worker
  reads the bot's updates (`getUpdates`, offset kept in `platform_settings`) and links that chat to the user who
  generated the code. No webhook is needed, so it works from a laptop.
- **Engine down:** the worker raises `engine_down` when a running strategy has not been stepped for 90 seconds, at
  most once an hour per run. It runs in the worker, not the engine, because the engine is what failed.
- Channels the server has no credentials for are shown as "not set up" in the UI instead of silently doing nothing.

Dry-run events are labelled "[dry run]". SMS and push are out of scope.
