# 0025 Signal sources: reading a Telegram tips channel and trading its direction

Status: Accepted, built phase by phase: A connect (done), B ingest + understand (done), C1 trade on paper, C2 approve /
live / replay.

**Context.** Users follow Telegram tips channels and want the platform to act on them without copying trades by hand.
The first channel ("Nifty Sensex VIP setups", 500 messages studied, 7 Sep - 8 Oct 2026) posts a signal as two
messages in the same minute: a header (`🟢 BUY NIFTY 22450 CE / 💰 Entry : ₹150 - ₹154 / 📊 Intraday Trade`) and a
reply with TP 1-3, stop-loss, rationale and validity. Updates reply to the header (`Target 2 done`, `STOP LOSS HIT`,
price ticks `₹163 🔥🔥🔥` = 63% of the traffic, a screenshot). Rare free text is advice (`trail sl near 143`, `EXIT
COMPLETELY`), noise (greetings, promos, call notices) or unclear (a one-off strangle idea). 43 signals, all BUY and
intraday, NIFTY 27 / SENSEX 16, near-ATM, 09:30-13:30; no expiry is ever named. The user wants only the direction (BUY
CE / SELL PE = bullish, BUY PE / SELL CE = bearish) and trades it as an option seller. A working read-only prototype
exists in algo-trading-claude (`telegram_signals/`, branch feature/telegram-signals): parser, Telethon reader,
catch-up + live feed, a signals page. It is ported, not rewritten.

**Decision.**

1. **The user's own Telegram account, read-only (MTProto via Telethon).** A bot cannot read a channel it does not
   administer, so the user logs in once through the web UI: API ID + hash (their own from my.telegram.org, or the
   platform's default app `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`), phone, the login code, and the 2-step password if
   set. The login runs in the API with a Telethon `StringSession`; between steps the half-finished session is kept
   encrypted on the source row (status `code_sent` / `password_needed`), never in the browser. The code and the 2FA
   password are used once and never stored or logged. After login the user picks one channel or group from their
   dialogs (id + title stored). Disconnect logs the session out at Telegram and wipes the secrets; history stays
   until the source is deleted. The session appears in the user's Telegram "Devices" as AlgoEarning and can be ended
   there; the platform then shows "reconnect Telegram".
2. **Secrets like broker credentials (ADR 0007):** `api_hash`, `phone` and `session` are envelope-encrypted with
   `ae_core.secrets.SecretBox`, authenticated data `signal_source:<id>:<field>`. API responses show only masked values
   (`+91 98•••••210`, API ID in clear, hash never); audit entries carry no secret.
3. **A full-access session is held, so access is fenced in code:** only the `ae_signals` process and the API's login
   endpoints open a client, through one wrapper (`TelegramReader`) that exposes connect / list dialogs / read history /
   listen / log out, nothing that sends, forwards, reacts or marks read; a test fails if any other Telethon method is
   called. FloodWait is honoured (status `flood_wait` with the time), an expired or revoked session flips the source to
   `needs_reconnect`, other errors back off and retry.
4. **Ingest (`python -m ae_signals`):** one process, a Redis lease (like the engine) so one copy runs; one client per
   connected source, catching up on the last ~200 messages at start, then new and edited messages live. Raw messages
   go to `signal_messages` exactly as received (text, date, edit date, reply_to, has_media) and are never modified, so
   history can always be re-parsed. Each change re-assembles that source's recent signals into `signals` and
   publishes them on Redis for the page and the engine.
5. **Understanding is pure and per channel (`ae_core.signals`):** a channel _profile_ turns a message into one kind:
   SIGNAL, DETAILS, TARGET, SL_HIT, TICK, MEDIA, ADVISORY, NOISE, UNCLEAR; the first profile is this channel's format
   (the prototype's parser). Updates attach by reply; a non-reply target / SL / advisory attaches to the latest
   signal. A signal is complete (tradable) only with index, strike, CE/PE, action and stop-loss; anything uncertain is
   UNCLEAR and never traded. Users can correct a classification ("correct this"): stored as an override that wins
   over the parser for that message and is kept as a test case for improving the profile.
6. **Trading through the existing engine and risk (ADR 0014, 0022):** a rules entry mode `signal` ("on a signal from
   <source>"), mapping the direction to legs: sell the opposite option (ATM / N strikes OTM / by premium), a credit
   spread (bull put / bear call, width in strikes), or follow the tip as given; plus the rules builder's lots, SL /
   target, trade-wide max loss / profit, time window, signals per day, a maximum signal age, and whether the channel's
   SL hit / target n / exit closes or trails the position. Modes per strategy: alert (notification via the outbox /
   Telegram bot), approve (notification + a "Place" button in the web app that expires after N seconds), auto. Paper
   first; live only with `signal_trading` and a connected broker, through the same live path and risk checks as every
   other strategy. Every step is a `trade_event`: signal received, mapped legs, risk result, orders, and every skip
   with its reason. Advisories never trade on their own.
7. **Replay:** a backtest of a signal strategy runs over the source's stored signals with historical prices (ADR
   0017), so a user sees how "sell the opposite side" would have done before going live. The replay never fills at the
   tip's entry range (the price had often moved by the time the message was posted): it enters our own legs at the
   market price of the minute after the message, with the usual slippage and charges; the tip's entry, SL and
   targets are shown beside it, and "follow the tip" reports how far the fill was from the tip's entry.
8. **Data and plans:** tables `signal_sources`, `signal_messages`, `signals`, `signal_overrides` (phase C2:
   `signal_approvals`) are user-owned with row-level security like every user table; entitlements
   `max_signal_sources` (limit) and `signal_trading` (flag). Nothing of one user's source is shared with another.

**Not in scope.** Posting to Telegram, reading chats the user is not a member of, sharing a source between users,
other messengers. Compliance points (third-party tips, auto-trading on them) are in `sebi_compliance_checklist.md`; the
signals page and the strategy builder show "signals come from a third party; you decide".

**Consequences.** The platform holds sessions that could act as the user on Telegram; the fence in (3), encryption,
masking and an easy disconnect are the mitigation, and the risk is stated where the user connects. Telethon becomes a
dependency of the API (login) and the new `ae_signals` process. A channel changing its format degrades to UNCLEAR
(nothing trades) until its profile is updated; raw history lets it be re-read.
