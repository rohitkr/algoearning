# SEBI Retail Algo SaaS Pre-Licensing & Broker Checklist

This document outlines the mandatory technical, risk management, and structural requirements mandated under SEBI regulations and Indian stock exchange (NSE/BSE) guidelines for retail algorithmic trading SaaS applications.

---

## 🌐 1. Infrastructure & Access Security

- [ ] **Static IP Routing Setup**: Ensure all live order execution requests are routed exclusively through dedicated static IPs. Dynamic cloud IPs are not allowed as brokers must whitelist your server IPs.
  - _Status: Partly. Orders go out from the home Mac's dynamic Hathway IP. The worker checks the public IP every 5 minutes and alerts Zerodha users when it changes (`apps/worker/src/ae_worker/public_ip.py`), and broker accounts show a static IP field, but no dedicated static IP is in place yet._
- [ ] **Mandatory Daily Session Resets**: Implement a script that completely invalidates all user authentication tokens, API sessions, and broker OAuth access daily before the market opens (9:00 AM IST).
  - _Status: Partly. Zerodha access tokens expire at 06:00 IST and the engine never uses an expired session (`apps/engine/src/ae_engine/engine.py`, `expires_at`). There is no job that revokes broker sessions or signs users out of the app before 09:00._
- [ ] **OAuth and Multi-Factor Auth (2FA)**: Ensure that your broker connection flows strictly use standard OAuth. Since you are using Clerk, ensure Clerk's Multi-Factor Authentication (MFA/2FA) is turned on to secure the entry gate to your application.
  - _Status: Partly. Zerodha is connected through Kite Connect's redirect login (`packages/py-brokers/src/ae_brokers/zerodha.py`). MFA is not enforced for app sign-in; it has to be switched on in the Clerk dashboard._

## 📉 2. Order Payload & Execution Restrictions

- [ ] **Algo-ID Database Mapping**: Every order database schema and API payload must feature a mandatory string field to append the 13-digit exchange-issued Strategy ID. Orders missing this tag will be blocked automatically by brokers.
  - _Status: Not done. Orders carry only our own 20-character tag (`tag_for` in `apps/engine/src/ae_engine/live.py`); there is no field for the exchange-issued Strategy/Algo ID._
- [x] **Order Type Blocks**: Hardcode validation to block users from selecting or triggering Market Orders or Immediate-Or-Cancel (IOC) orders via the algo. Logic must exclusively use strictly defined Limit or Stop-Limit orders to prevent retail flash slippage.
  - _Status: Done. Real orders are always LIMIT (marketable limit at LTP +/- 2%, re-priced, then cancelled), never MARKET or IOC (`apps/engine/src/ae_engine/live.py`). MARKET appears only on paper/dry-run records._
- [ ] **Order Throttling (OPS Cap)**: Build an asynchronous rate limiter into your order placement queue. Enforce a strict Orders Per Second (OPS) ceiling per individual client account to avoid high-frequency trading (HFT) system triggers.
  - _Status: Not done. Orders are placed one after another per account, but there is no explicit orders-per-second limit._

## 🛡️ 3. Pre-Trade Risk Management System (RMS)

- [ ] **Hardcoded Risk Bounds**: The backend engine must evaluate and reject orders _before_ dispatch if they breach the following limits:
  1. Maximum quantity per single order execution.
  2. Maximum monetary exposure/gross value per single trade.
  3. Daily maximum loss or maximum drawdown cap per client account.
  - _Status: Partly. (1) Max lots per order from the user's plan and (3) daily max loss, max trades per day, max open positions and a kill switch are enforced before dispatch (`packages/py-core/src/ae_core/trading/risk.py`). (2) There is no max monetary value per trade; only a basket margin check against available funds._
- [ ] **Price Band Validation**: Implement a pre-check script that cross-references the current Last Traded Price (LTP) from your data stream to block orders positioned outside the exchange's active daily price bands.
  - _Status: Not done. Limit prices come from the feed's LTP, but nothing checks them against the exchange's daily price bands (circuit limits)._

## 📊 4. Data Logging & Interface Disclosures

- [ ] **5-Year Immutable Audit Trail**: Store structural logs of all core algorithmic calculations, payload responses, system logs, version updates, and user modifications in an immutable time-series data layer (e.g., PostgreSQL/TimescaleDB) retrievable for 5 years.
  - _Status: Partly. `audit_log` is append-only (a trigger rejects UPDATE/DELETE) and `trade_events` records every trade's transitions and fills. Missing: a 5-year retention policy, logs of strategy calculations and broker payloads, and version history._
- [x] **Whitebox Code Transparency**: Frontend dashboards must explicitly itemize and present all technical indicators, parameters, entry/exit math logic, and formulas driving the strategy so it remains purely "whitebox".
  - _Status: Done. Every strategy's indicators, parameters and entry/exit rules are visible and editable in the strategy builder, and SMC signals show the setup that produced them (`apps/web/components/smc/signal-card.tsx`)._
- [ ] **Prohibition of Performance Marketing**: Completely remove any features, banners, or widgets that present simulated historical backtesting percentages, guaranteed yields, or target returns aimed at attracting retail users.
  - _Status: Not done. The product page on app.algoearning.com (`apps/web/landing/index.html`) shows sample P&L figures (for example +₹4,820) in its dashboard mock-up. It has no backtest percentages or promised returns, and it carries a risk disclaimer, but the sample profits should go._

---

_Reviewed against the code on 2 October 2026 (commit c2224a1). [x] = covered; the status line under each item says what exists and what is missing._

_Disclaimer: This checklist reflects common technical requirements requested during broker integrations and exchange audits based on standard SEBI retail algorithmic guidelines._
