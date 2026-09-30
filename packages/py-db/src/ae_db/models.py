"""Schema. Every user-owned table carries user_id (indexed, FK with ON DELETE CASCADE) and is protected twice:
repositories always filter by the caller's user_id, and PostgreSQL row-level security (see rls.py) hides
other users' rows even from a query that forgets to.

Money and prices are NUMERIC (exact): prices (14,4) cover MCX ticks like 0.05 / 0.1 / 0.5; amounts (16,2).
Trade and order columns follow the local app's trader/repository.py so its engine ports over in phase 9."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamps, UUIDPk
from .enums import (
    BillingKind,
    BillingOrderStatus,
    Broker,
    BrokerAccountStatus,
    OrderKind,
    PlanInterval,
    RunStatus,
    Side,
    StrategyStatus,
    SubscriptionStatus,
    TradingMode,
    UserRole,
    UserStatus,
)

PRICE = Numeric(14, 4)
AMOUNT = Numeric(16, 2)


def enum_col(e: type, name: str) -> Enum:
    return Enum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda x: [m.value for m in x],
    )


def owner() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)


# -- users & auth --------------------------------------------------------------------------------------
class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"
    auth_subject: Mapped[str] = mapped_column(String(191), unique=True)  # the auth provider's user id (Clerk)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-cased
    name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    role: Mapped[UserRole] = mapped_column(enum_col(UserRole, "user_role"), default=UserRole.USER)
    status: Mapped[UserStatus] = mapped_column(enum_col(UserStatus, "user_status"), default=UserStatus.ACTIVE)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -- plans & billing --------------------------------------------------------------------------------------
class Plan(UUIDPk, Timestamps, Base):
    """A sellable plan. Limits/features are data (admin-editable), read by the entitlement service (phase 5)."""

    __tablename__ = "plans"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    price_paise: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    interval: Mapped[PlanInterval] = mapped_column(enum_col(PlanInterval, "plan_interval"), default=PlanInterval.MONTH)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    provider_plan_id: Mapped[str | None] = mapped_column(String(100), unique=True)


class Subscription(UUIDPk, Timestamps, Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[uuid.UUID] = owner()
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("plans.id", ondelete="RESTRICT"))
    status: Mapped[SubscriptionStatus] = mapped_column(enum_col(SubscriptionStatus, "subscription_status"))
    provider: Mapped[str] = mapped_column(String(20), default="razorpay")
    provider_subscription_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[BillingKind] = mapped_column(
        enum_col(BillingKind, "billing_kind"), default=BillingKind.PREPAID, server_default=BillingKind.PREPAID.value
    )
    plan: Mapped[Plan] = relationship(lazy="joined")


class Payment(UUIDPk, Base):
    __tablename__ = "payments"
    user_id: Mapped[uuid.UUID] = owner()
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("subscriptions.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(20), default="razorpay")
    provider_payment_id: Mapped[str] = mapped_column(String(100), unique=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingOrder(UUIDPk, Timestamps, Base):
    """One checkout: the provider order created for a plan purchase, and what became of it. The amount is fixed
    server-side from the plan when the order is created; a payment must match it exactly to be applied."""

    __tablename__ = "billing_orders"
    user_id: Mapped[uuid.UUID] = owner()
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("plans.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(20), default="razorpay")
    provider_order_id: Mapped[str] = mapped_column(String(60), unique=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[BillingOrderStatus] = mapped_column(
        enum_col(BillingOrderStatus, "billing_order_status"), default=BillingOrderStatus.CREATED
    )
    provider_payment_id: Mapped[str | None] = mapped_column(String(60), unique=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("subscriptions.id", ondelete="SET NULL"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # proration applied, failure reason
    plan: Mapped[Plan] = relationship(lazy="joined")


class WebhookEvent(Base):
    """Every provider webhook, stored once (unique event id) before processing: replays are ignored."""

    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("provider", "event_id"),)


# -- brokers --------------------------------------------------------------------------------------------
class BrokerAccount(UUIDPk, Timestamps, Base):
    """One connected broker login. A user may have several (e.g. two Zerodha + one Upstox). Credentials are
    ciphertext (envelope-encrypted in phase 7); they are never selected into API responses."""

    __tablename__ = "broker_accounts"
    user_id: Mapped[uuid.UUID] = owner()
    broker: Mapped[Broker] = mapped_column(enum_col(Broker, "broker"))
    client_id: Mapped[str] = mapped_column(String(40))  # the broker's user id, e.g. AB1234
    label: Mapped[str | None] = mapped_column(String(80))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    api_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    static_ip: Mapped[str | None] = mapped_column(INET)
    terminal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    engine_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[BrokerAccountStatus] = mapped_column(
        enum_col(BrokerAccountStatus, "broker_account_status"), default=BrokerAccountStatus.DISCONNECTED
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "broker", "client_id"),)


class BrokerSession(UUIDPk, Base):
    """The broker's daily access token for one account (ciphertext), replaced on every login."""

    __tablename__ = "broker_sessions"
    user_id: Mapped[uuid.UUID] = owner()
    broker_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("broker_accounts.id", ondelete="CASCADE"), unique=True
    )
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# -- strategies -------------------------------------------------------------------------------------------
class Strategy(UUIDPk, Timestamps, Base):
    __tablename__ = "strategies"
    user_id: Mapped[uuid.UUID] = owner()
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), default="options_multileg")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # legs, SL/TP, timing, exits
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[StrategyStatus] = mapped_column(
        enum_col(StrategyStatus, "strategy_status"), default=StrategyStatus.DRAFT
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_strategies_user_created", "user_id", "created_at", "id"),)


class StrategyRun(UUIDPk, Timestamps, Base):
    """One deployment of a strategy (paper or live) on a broker account, with the exact config it ran."""

    __tablename__ = "strategy_runs"
    user_id: Mapped[uuid.UUID] = owner()
    strategy_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    broker_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("broker_accounts.id", ondelete="SET NULL")
    )
    mode: Mapped[TradingMode] = mapped_column(enum_col(TradingMode, "trading_mode"))
    status: Mapped[RunStatus] = mapped_column(enum_col(RunStatus, "run_status"), default=RunStatus.PENDING)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    error: Mapped[str | None] = mapped_column(Text)


# -- trading ----------------------------------------------------------------------------------------------
class Trade(UUIDPk, Timestamps, Base):
    """One position leg through its lifecycle (the local app's `trades` row, per user)."""

    __tablename__ = "trades"
    user_id: Mapped[uuid.UUID] = owner()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("strategy_runs.id", ondelete="SET NULL"), index=True
    )
    broker_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("broker_accounts.id", ondelete="SET NULL")
    )
    mode: Mapped[TradingMode] = mapped_column(enum_col(TradingMode, "trading_mode"))
    status: Mapped[str] = mapped_column(String(40))  # trade lifecycle state (py-core)
    trade_date: Mapped[date] = mapped_column(Date)
    underlying: Mapped[str] = mapped_column(String(30))
    exchange: Mapped[str] = mapped_column(String(10))
    tradingsymbol: Mapped[str] = mapped_column(String(60))
    expiry: Mapped[date] = mapped_column(Date)
    strike: Mapped[Decimal] = mapped_column(PRICE)
    option_type: Mapped[str] = mapped_column(String(2))
    side: Mapped[Side] = mapped_column(enum_col(Side, "side"))
    product: Mapped[str] = mapped_column(String(10))
    lots: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer)  # units per lot (MCX: barrels, 10 g)
    quantity: Mapped[int] = mapped_column(Integer)  # units
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    entry_avg_price: Mapped[Decimal | None] = mapped_column(PRICE)
    initial_sl: Mapped[Decimal | None] = mapped_column(PRICE)
    current_sl: Mapped[Decimal | None] = mapped_column(PRICE)
    target: Mapped[Decimal | None] = mapped_column(PRICE)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    exited_qty: Mapped[int] = mapped_column(Integer, default=0)
    open_qty: Mapped[int] = mapped_column(Integer, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    unrealized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    last_ltp: Mapped[Decimal | None] = mapped_column(PRICE)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # trailing / partial / auto-exit settings
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    exit_avg_price: Mapped[Decimal | None] = mapped_column(PRICE)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_trades_user_date", "user_id", "trade_date"),)


class Order(UUIDPk, Timestamps, Base):
    __tablename__ = "orders"
    user_id: Mapped[uuid.UUID] = owner()
    trade_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    broker_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("broker_accounts.id", ondelete="SET NULL")
    )
    kind: Mapped[OrderKind] = mapped_column(enum_col(OrderKind, "order_kind"))
    tag: Mapped[str] = mapped_column(String(20), unique=True)  # our idempotency tag sent to the broker
    broker_order_id: Mapped[str | None] = mapped_column(String(60))
    side: Mapped[Side] = mapped_column(enum_col(Side, "side"))
    order_type: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    trigger_price: Mapped[Decimal | None] = mapped_column(PRICE)
    status: Mapped[str] = mapped_column(String(30))
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[Decimal | None] = mapped_column(PRICE)
    status_message: Mapped[str | None] = mapped_column(Text)
    reprices: Mapped[int] = mapped_column(Integer, default=0)
    modifications: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("broker_account_id", "broker_order_id"),)


class TradeEvent(Base):
    """Per-trade audit trail (state transitions, fills, rule triggers), as in the local app."""

    __tablename__ = "trade_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = owner()
    trade_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("strategy_runs.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(60))
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# -- audit --------------------------------------------------------------------------------------------------
class AuditLog(Base):
    """Append-only security audit (a trigger rejects UPDATE/DELETE): who did what to which object, from where."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(40), default="user")  # user | system | admin | webhook
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(400))
    request_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
