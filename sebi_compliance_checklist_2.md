# AlgoEarnin — Retail Algo Compliance Checklist

**Scope:** White-box, no-code retail algorithmic trading platform  
**Target model:** Client-created strategies through Broker Client Direct API  
**Framework reference date:** 2 October 2026

> **Important:** White-box/no-code does not by itself determine AlgoEarnin's regulatory classification. Before production launch, confirm the operating model, developer classification, API arrangement, hosting model, strategy registration/tagging and responsibilities with each participating broker and, where applicable, the exchange.

---

## 1. Regulatory Classification & Operating Model

- [ ] Define AlgoEarnin's exact regulatory/operating role:
  - Client-created strategy platform
  - Technology/vendor platform
  - Algo Provider
  - Broker-integrated application/ASP
  - Combination depending on broker
- [ ] Document whether the retail client is the strategy developer/owner.
- [ ] Document who controls strategy logic and parameters.
- [ ] Document where strategy execution occurs.
- [ ] Document which entity owns/controls the execution infrastructure.
- [ ] Obtain written confirmation from each participating broker regarding:
  - Supported retail-algo model
  - Client Direct API availability
  - Whether AlgoEarnin requires onboarding/approval/empanelment
  - Who registers the strategy
  - Who owns the Algo ID
  - Static-IP ownership
  - Required API tagging
  - Applicable order restrictions
- [ ] Maintain the regulatory operating model as controlled documentation.

---

## 2. Client & Broker Authentication

- [ ] Use only the broker's supported OAuth/authentication mechanism.
- [ ] Enforce applicable two-factor authentication.
- [ ] Do not expose generic/open order APIs that bypass broker authorization.
- [ ] Use unique broker API credentials/application associations per client/broker connection.
- [ ] Securely encrypt broker credentials, secrets and access tokens.
- [ ] Never store broker credentials/secrets in plaintext.
- [ ] Implement broker API session lifecycle management.
- [ ] Implement the required daily broker API session logout/invalidation.
- [ ] Maintain authentication/session audit events.

---

## 3. Static IP

- [ ] Use a dedicated static IP for production broker API access.
- [ ] Ensure the broker whitelists the required static IP.
- [ ] Support primary/secondary infrastructure where required by the broker/exchange.
- [ ] Detect configured/public IP changes.
- [ ] Stop new orders when the configured execution IP is invalid or no longer authorized.
- [ ] Alert administrators/users when the execution IP changes.
- [ ] Require reauthorization/re-whitelisting before resuming live execution.
- [ ] Do not rely on a residential dynamic IP for production execution.

---

## 4. White-Box Strategy Requirements

- [ ] Expose all strategy indicators.
- [ ] Expose all strategy parameters.
- [ ] Expose entry conditions.
- [ ] Expose exit conditions.
- [ ] Expose stop-loss logic.
- [ ] Expose target/profit-taking logic.
- [ ] Expose position-sizing logic.
- [ ] Expose filters and conditions.
- [ ] Expose timeframe.
- [ ] Expose instrument/universe.
- [ ] Ensure strategy logic is transparent and replicable.
- [ ] Prevent hidden execution logic from changing the user's disclosed strategy.
- [ ] If AI assists strategy creation, expose the final executable rules.
- [ ] Provide a strategy definition export/download.
- [ ] Store an immutable representation/hash of each live strategy version.

---

## 5. Strategy Registration / Algo ID

- [ ] Do not hardcode a single universal "13-digit Strategy ID" model.
- [ ] Design an exchange-aware strategy/algo registration model containing, where applicable:
  - Exchange
  - Segment
  - Broker
  - Internal strategy ID
  - Exchange Algo ID
  - API setup name
  - API version
  - Registration date
  - Registration status
- [ ] Determine whether each strategy requires registration based on the applicable developer/category/OPS rules.
- [ ] Correctly distinguish API orders that are algo orders even when a particular registration exemption applies.
- [ ] Apply the broker/exchange-required algo tagging.
- [ ] Block live deployment when required registration/tagging information is missing.
- [ ] Keep exchange/broker registration records in the audit trail.

---

## 6. Order Throttling

- [ ] Implement a server-side order rate limiter.
- [ ] Enforce the applicable OPS ceiling per exchange/segment.
- [ ] Account for broker-imposed lower client-level limits.
- [ ] Make the limiter atomic/distributed so concurrent workers cannot bypass it.
- [ ] Prevent burst orders from bypassing the limit.
- [ ] Handle broker rate-limit rejection safely.
- [ ] Do not blindly retry rejected orders and create an order storm.
- [ ] Log rate-limit decisions and rejections.

---

## 7. Pre-Trade Risk Management

### Individual Order

- [ ] Maximum quantity per order.
- [ ] Maximum monetary/order value.
- [ ] Price validation.
- [ ] Exchange price-band validation.
- [ ] Allowed order-type validation.
- [ ] Instrument/contract validity check.
- [ ] Duplicate-order protection.

### Client/Account

- [ ] Maximum gross exposure.
- [ ] Maximum open positions.
- [ ] Maximum daily loss.
- [ ] Maximum daily trades.
- [ ] Maximum strategy exposure.
- [ ] Maximum symbol/instrument exposure.
- [ ] Available-funds/margin validation.

### System

- [ ] Global kill switch.
- [ ] Client kill switch.
- [ ] Strategy kill switch.
- [ ] Broker kill switch.
- [ ] Order-rate kill switch.
- [ ] Automatic risk-triggered shutdown.

All applicable risk checks must occur **before order dispatch**.

---

## 8. Price-Band Protection

- [ ] Obtain the applicable exchange price-band/circuit information.
- [ ] Validate every order price against the applicable exchange limits before dispatch.
- [ ] Reject orders outside permitted price ranges.
- [ ] Handle dynamic/changed price bands.
- [ ] Log the price-band check and rejection reason.
- [ ] Do not rely solely on an LTP +/- percentage rule.

---

## 9. Order-Type Restrictions

- [ ] Block prohibited algo market orders.
- [ ] Maintain exchange/segment-specific order-type rules.
- [ ] Maintain broker-specific restrictions.
- [ ] Do not assume every order-type restriction is universal across all exchanges/segments.
- [ ] Validate order type immediately before broker dispatch.
- [ ] Reject invalid order types before sending them to the broker.
- [ ] Log order-type validation failures.

---

## 10. Order Lifecycle & Reconciliation

- [ ] Continuously reconcile AlgoEarnin state with broker orders.
- [ ] Reconcile broker trades/fills.
- [ ] Reconcile actual broker positions.
- [ ] Detect manual broker-side order/position changes.
- [ ] Detect manual exits.
- [ ] Never issue a duplicate exit when the broker already closed the position.
- [ ] Handle partial fills.
- [ ] Handle rejected orders.
- [ ] Handle cancelled orders.
- [ ] Handle modified orders.
- [ ] Handle broker API disconnects.
- [ ] Handle WebSocket disconnect/reconnect.
- [ ] Reconcile after application restart.
- [ ] Reconcile after worker/engine crash.
- [ ] Reconcile before resuming a strategy.
- [ ] Store broker order IDs and trade IDs.
- [ ] Maintain an immutable order-state transition history.

---

## 11. Kill Switch

Implement multiple levels:

- [ ] Global platform kill switch.
- [ ] Broker-level kill switch.
- [ ] Client/account kill switch.
- [ ] Strategy-level kill switch.
- [ ] Instrument/symbol kill switch where useful.
- [ ] Individual order protection.

Automatic triggers should include, where applicable:

- [ ] Daily loss exceeded.
- [ ] Exposure exceeded.
- [ ] Position mismatch.
- [ ] Broker connectivity inconsistency.
- [ ] Market-data failure.
- [ ] Unexpected order rejection spike.
- [ ] Duplicate-order detection.
- [ ] Abnormal order rate.
- [ ] System integrity failure.

Define whether risk-reducing exits remain permitted after new-order execution is disabled.

---

## 12. Audit Trail

Maintain an immutable, searchable audit trail for the applicable retention period.

### User Identity

- [ ] User ID.
- [ ] Client/UCC identifier where applicable.
- [ ] Broker account.
- [ ] Authentication/session events.

### Strategy

- [ ] Strategy ID.
- [ ] Algo ID.
- [ ] Strategy version.
- [ ] Strategy configuration.
- [ ] Logic/configuration hash.
- [ ] Strategy activation/deactivation.

### Signal/Decision

- [ ] Signal timestamp.
- [ ] Market-data reference.
- [ ] Indicator values used.
- [ ] Strategy conditions evaluated.
- [ ] Signal generated.
- [ ] Decision taken.
- [ ] Decision/rejection reason.

### Risk

- [ ] Risk checks performed.
- [ ] Risk-check results.
- [ ] Risk rejection reason.
- [ ] Risk configuration version.

### Order

- [ ] Order request.
- [ ] Order payload.
- [ ] Timestamp.
- [ ] Symbol/instrument.
- [ ] Side.
- [ ] Quantity.
- [ ] Price.
- [ ] Order type.
- [ ] Algo tagging/ID.
- [ ] Broker response.
- [ ] Broker order ID.
- [ ] Order-state transitions.

### Trade

- [ ] Fill quantity.
- [ ] Fill price.
- [ ] Fill timestamp.
- [ ] Broker trade ID.
- [ ] Position transition.

### Administration

- [ ] Strategy changes.
- [ ] Risk changes.
- [ ] API connection/disconnection.
- [ ] User/admin actions.
- [ ] Kill-switch activation.
- [ ] Deployment.
- [ ] Configuration changes.

### Retention

- [ ] Maintain required records for at least the applicable five-year period.
- [ ] Prevent unauthorized UPDATE/DELETE of immutable audit records.
- [ ] Back up audit records.
- [ ] Test restoration/retrieval.
- [ ] Monitor retention-policy compliance.

---

## 13. Strategy Versioning & Change Management

- [ ] Every live strategy has an immutable version.
- [ ] Configuration changes create a new version.
- [ ] Never silently mutate an already-deployed strategy.
- [ ] Generate a logic/configuration hash.
- [ ] Record who changed the strategy.
- [ ] Record when it changed.
- [ ] Store old and new versions.
- [ ] Record deployment time.
- [ ] Record deployment actor.
- [ ] Record rollback information.
- [ ] Prevent unapproved versions from becoming live.
- [ ] Determine whether a strategy change requires broker/exchange re-registration.
- [ ] Block deployment until required registration/approval is complete.

---

## 14. Testing & Certification

- [ ] Unit tests.
- [ ] Strategy-rule tests.
- [ ] Indicator calculation tests.
- [ ] RMS tests.
- [ ] Broker API integration tests.
- [ ] Order rejection tests.
- [ ] Duplicate-order tests.
- [ ] Partial-fill tests.
- [ ] Manual-exit tests.
- [ ] Network failure tests.
- [ ] WebSocket failure tests.
- [ ] Broker outage tests.
- [ ] Application restart tests.
- [ ] Database recovery tests.
- [ ] Kill-switch tests.
- [ ] Static-IP failure tests.
- [ ] Price-band tests.
- [ ] OPS/rate-limit tests.
- [ ] UAT.
- [ ] Broker/exchange mock testing where applicable.
- [ ] Maintain test evidence.
- [ ] Regression-test every material strategy-engine change.

---

## 15. Cybersecurity

- [ ] MFA.
- [ ] Role-based access control.
- [ ] Least-privilege access.
- [ ] Production/admin access restrictions.
- [ ] Encryption in transit.
- [ ] Encryption at rest.
- [ ] Secure secret management.
- [ ] Broker token encryption.
- [ ] No credentials/secrets in source control.
- [ ] Dependency/security scanning.
- [ ] Vulnerability management.
- [ ] Security monitoring.
- [ ] Admin activity logging.
- [ ] Production access logging.
- [ ] Incident detection.
- [ ] Incident response procedure.
- [ ] Security incident escalation.
- [ ] Backup controls.
- [ ] Disaster recovery.
- [ ] Recovery testing.
- [ ] Security testing/VAPT as applicable.
- [ ] Maintain security evidence and reports.

---

## 16. Infrastructure, BCP & Disaster Recovery

- [ ] Production-grade hosting.
- [ ] Dedicated static IP.
- [ ] Infrastructure monitoring.
- [ ] Database backups.
- [ ] Off-system backup where appropriate.
- [ ] Disaster-recovery environment.
- [ ] Defined RPO.
- [ ] Defined RTO.
- [ ] Recovery procedures.
- [ ] DR testing.
- [ ] Broker outage handling.
- [ ] Market-data outage handling.
- [ ] Queue failure handling.
- [ ] Worker/engine failure handling.
- [ ] Duplicate-order prevention after recovery.
- [ ] Position reconciliation after recovery.
- [ ] Order reconciliation after recovery.
- [ ] Reliable system-clock synchronization.

---

## 17. Market-Data & Instrument Integrity

- [ ] Identify market-data source.
- [ ] Validate market-data timestamps.
- [ ] Detect stale data.
- [ ] Detect missing candles/ticks.
- [ ] Detect duplicate data.
- [ ] Detect out-of-order data.
- [ ] Detect feed disconnects.
- [ ] Stop new signals/orders on unacceptable data quality.
- [ ] Validate instrument/master data.
- [ ] Prevent trading expired contracts.
- [ ] Validate option strike/expiry.
- [ ] Handle contract/master-data changes.
- [ ] Maintain data-quality audit events.

Example protection:

```text
Market data is stale
        ↓
STOP NEW SIGNALS
        ↓
STOP NEW ORDERS
        ↓
ALERT
        ↓
RECOVER + VALIDATE
        ↓
RESUME
```

---

## 18. Customer / Investor Disclosures

- [ ] Clearly explain that the user controls their strategy.
- [ ] Clearly explain trading risks.
- [ ] No guaranteed returns.
- [ ] No assured profits.
- [ ] No misleading performance claims.
- [ ] No misleading win-rate claims.
- [ ] No cherry-picked backtests presented as expected results.
- [ ] No simulated P&L presented as actual live performance.
- [ ] No fixed monthly-return claims.
- [ ] No "passive income" claims implying assured income.
- [ ] Clearly distinguish:
  - Backtest
  - Paper trading
  - Simulation
  - Live trading
- [ ] Clearly disclose fees.
- [ ] Clearly disclose broker/exchange dependencies.
- [ ] Clearly disclose technology/service limitations.
- [ ] Maintain appropriate risk disclosures and terms.

**Remove sample marketing P&L such as `+₹4,820` from the landing page unless there is a clearly compliant and justified presentation.**

---

## 19. White-Box / No-Code Guardrails

- [ ] Every live strategy has visible logic.
- [ ] Every live strategy has visible parameters.
- [ ] User can inspect the complete rule set.
- [ ] User can reproduce the strategy logic.
- [ ] No hidden signal-generation logic.
- [ ] No hidden execution conditions.
- [ ] AI-generated strategies expose final executable rules.
- [ ] AI cannot silently modify a live strategy.
- [ ] Strategy changes require explicit user action/controlled deployment.
- [ ] Store the exact rule definition used for every live order.

---

## 20. Black-Box Strategy Prevention

If AlgoEarnin's intended product is strictly white-box:

- [ ] Do not offer hidden black-box strategies.
- [ ] Do not sell undisclosed proprietary signals as executable strategies.
- [ ] Do not allow third-party strategy providers to hide execution logic.
- [ ] Do not describe a strategy as white-box if users cannot reproduce its rules.
- [ ] If black-box functionality is ever introduced, stop and perform a separate regulatory assessment before implementation.
- [ ] Assess applicable Research Analyst / Algo Provider requirements before offering black-box strategies.

---

## 21. Grievance & Support

- [ ] Customer support process.
- [ ] Trading-incident reporting process.
- [ ] Broker escalation process.
- [ ] Complaint records.
- [ ] Resolution tracking.
- [ ] Defined SLA for trading incidents.
- [ ] Preserve relevant logs when a complaint is raised.
- [ ] Clearly define AlgoEarnin's responsibility for:
  - Platform/software issues
  - Strategy configuration
  - Order-routing technology
- [ ] Clearly distinguish broker responsibility for:
  - Trading account
  - Broker RMS
  - Order acceptance/rejection
  - Exchange connectivity
  - Execution

---

# 22. Broker / Exchange Integration Matrix

Maintain a configuration matrix per broker:

| Attribute | Broker A | Broker B | Broker C |
|---|---|---|---|
| Client Direct API | ☐ | ☐ | ☐ |
| Static IP | ☐ | ☐ | ☐ |
| OAuth | ☐ | ☐ | ☐ |
| 2FA | ☐ | ☐ | ☐ |
| Daily API logout | ☐ | ☐ | ☐ |
| Algo registration | ☐ | ☐ | ☐ |
| Algo ID | ☐ | ☐ | ☐ |
| API tagging | ☐ | ☐ | ☐ |
| OPS limit | ☐ | ☐ | ☐ |
| Market-order restriction | ☐ | ☐ | ☐ |
| IOC restriction | ☐ | ☐ | ☐ |
| Broker RMS | ☐ | ☐ | ☐ |
| Price-band handling | ☐ | ☐ | ☐ |
| Mock testing | ☐ | ☐ | ☐ |
| Production approval | ☐ | ☐ | ☐ |

Do not assume that one broker's implementation is identical to another broker's implementation.

---

# 23. Recommended AlgoEarnin Production Architecture

```text
                    RETAIL CLIENT
                         │
                         ▼
              ┌─────────────────────┐
              │ AlgoEarnin Web App  │
              │                     │
              │ Strategy Builder    │
              │ Risk Configuration  │
              │ Broker Connection   │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ Strategy Engine     │
              │                     │
              │ White-box Rules     │
              │ Signal Generation   │
              │ Strategy Versioning │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ Pre-Trade RMS       │
              │                     │
              │ Quantity            │
              │ Exposure            │
              │ Daily Loss          │
              │ Price Band          │
              │ OPS                 │
              │ Order Type          │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ Order Gateway       │
              │                     │
              │ Static IP           │
              │ Algo Tag            │
              │ Broker API          │
              └──────────┬──────────┘
                         │
                         ▼
                    BROKER API
                         │
                         ▼
                   NSE / BSE
                         │
                         ▼
              ┌─────────────────────┐
              │ Reconciliation      │
              │                     │
              │ Orders              │
              │ Trades              │
              │ Positions           │
              │ Manual Changes      │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ Immutable Audit     │
              │                     │
              │ Signals             │
              │ RMS                 │
              │ Orders              │
              │ Trades              │
              │ Changes             │
              │ Admin Actions       │
              └─────────────────────┘
```

---

# 24. Current AlgoEarnin Gap Assessment

Based on the implementation status reviewed on 2 October 2026:

| Area | Status |
|---|---|
| White-box strategy | 🟢 |
| Strategy parameters/rules UI | 🟢 |
| Basic client RMS | 🟢 |
| Daily loss | 🟢 |
| Maximum trades | 🟢 |
| Maximum positions | 🟢 |
| Kill switch | 🟢 |
| Static IP | 🔴 |
| Broker/API session lifecycle | 🟡 |
| Exchange/API tagging | 🔴 |
| OPS limiter | 🔴 |
| Price-band validation | 🔴 |
| Maximum monetary order value | 🔴 |
| Five-year retention policy | 🔴 |
| Signal/decision audit | 🔴 |
| Broker payload audit | 🔴 |
| Strategy versioning | 🔴 |
| Deployment/change audit | 🔴 |
| Order reconciliation | 🟡 |
| Manual-exit detection | 🟡 |
| Partial-fill recovery | 🟡 |
| Cybersecurity controls | 🟡 |
| Disaster recovery / BCP | 🔴 |
| Failure/recovery testing | 🔴 |
| Performance marketing cleanup | 🔴 |
| Black-box prevention | 🟡 |
| Grievance workflow | 🔴 |

---

# 25. Important Regulatory Position

This checklist is a **technical and operational readiness checklist**, not a legal certification.

Before AlgoEarnin enables live retail algo trading, obtain confirmation from:

1. The participating broker's compliance/API team.
2. The applicable exchange where required.
3. An Indian securities-law/compliance professional.

The final production architecture should be based on the **actual broker/exchange classification of AlgoEarnin**, rather than assuming that white-box/no-code automatically qualifies it as a Tech-Savvy Retail Investor's own algo.

---

## Key References

- SEBI — Safer participation of retail investors in Algorithmic Trading.
- NSE — Retail Algo implementation/FAQ and Client Direct API requirements.
- NSE — Empanelled Algo Provider / algorithmic trading implementation material.
- Applicable broker-specific API and retail algo documentation.

**Last reviewed:** 2 October 2026
