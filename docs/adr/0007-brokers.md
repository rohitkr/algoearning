# 0007 Brokers: multi-account, BrokerAdapter, envelope-encrypted credentials

**Decision.** A user can connect several broker accounts (e.g. two Zerodha + one Upstox). Broker APIs are used for
execution only (login, orders, order book, positions, margins); prices come from the platform feed (ADR 0006).
`ae_brokers.base.BrokerAdapter` (login_url, exchange, profile, logout; execution methods join in phase 9) is
implemented for Zerodha; Upstox / Angel One are catalogued as "coming soon". Plan limit: `max_broker_accounts`.

**Credentials** (`ae_core.secrets`): API key, API secret and the daily access token are envelope-encrypted: a fresh
AES-256-GCM data key per value, wrapped by the master key `APP_ENCRYPTION_KEY` (a KMS key later; key versions allow
rotation). The row + field (`broker_account:<id>:api_secret`) is authenticated data, so ciphertext copied to another
row or user does not decrypt. Plaintext never reaches the browser, logs or audit entries: responses carry only a
masked API key.

**Daily login.** `POST /v1/broker-accounts/{id}/login` returns the broker login URL with a signed `state`
(HMAC key derived from the master key; account + user + 10-minute expiry + nonce consumed once from Redis). The broker
redirects the browser to `GET /v1/brokers/{broker}/callback` (public; everything is proven by the state), which
exchanges the one-time token server-side, refuses a login by a different broker account than the registered client
id (and invalidates that session), stores the session encrypted with its 06:00 IST expiry, and redirects to the web
app. Terminal off = logout at the broker; the Trading Engine switch requires a connected terminal.

**Static IPs** (SEBI) are stored per account (`static_ip`) and shown on the Broker page; assignment is an ops task
(phase 15).
