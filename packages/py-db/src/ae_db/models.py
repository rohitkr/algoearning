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
    Float,
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


class Instrument(UUIDPk, Timestamps, Base):
    """An underlying strategies can trade. Exchange facts (lot size, strike step, expiry type) are refreshed daily from
    the broker's instrument list; trading hours are set by admins. Read-only for users (ADR 0011)."""

    __tablename__ = "instruments"
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    exchange: Mapped[str] = mapped_column(String(10))  # derivatives segment: NFO, BFO
    lot_size: Mapped[int] = mapped_column(Integer)
    strike_step: Mapped[int] = mapped_column(Integer)
    weekly_expiry: Mapped[bool] = mapped_column(Boolean)
    session_open: Mapped[str] = mapped_column(String(5), default="09:15")  # HH:MM IST
    session_close: Mapped[str] = mapped_column(String(5), default="15:40")
    spot_exchange: Mapped[str] = mapped_column(
        String(10), default="NSE", server_default="NSE"
    )  # where the index itself trades
    feed_code: Mapped[str | None] = mapped_column(String(20))  # the market-data feed's code (Breeze stock_code)
    kite_symbol: Mapped[str | None] = mapped_column(String(40))  # the index's Kite tradingsymbol, for a Kite feed
    expiries: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")  # listed option expiries
    freeze_qty: Mapped[int] = mapped_column(Integer, default=1800, server_default="1800")  # max units per order
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(20), default="seed")  # seed | kite | admin: who set the facts last
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformSecret(UUIDPk, Timestamps, Base):
    """A platform credential set at runtime by an admin, envelope-encrypted (ae_core.secrets), e.g. the day's Breeze
    market-data session. System only: users have no privileges on this table."""

    __tablename__ = "platform_secrets"
    name: Mapped[str] = mapped_column(String(60), unique=True)
    value_enc: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class UserOverride(UUIDPk, Timestamps, Base):
    """An admin's per-user feature overrides on top of the plan (validated by ae_core.entitlements).
    Users can read their own row (it shapes what they may do) but only the system writes it."""

    __tablename__ = "user_overrides"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    note: Mapped[str | None] = mapped_column(Text)  # why (shown to admins only)
    live_unlocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # may send real orders
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


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


class SignalSource(UUIDPk, Timestamps, Base):
    """A user's Telegram login and the one chat it reads tips from (ADR 0025). The session, API hash and phone are
    ciphertext (SecretBox, authenticated data `signal_source:<id>:<field>`) and never leave the server; `login_state`
    holds the code request's hash between the login steps. api_hash_enc is empty when the platform's own Telegram
    app is used. status: code_sent | password_needed | connected | flood_wait | needs_reconnect | disconnected."""

    __tablename__ = "signal_sources"
    user_id: Mapped[uuid.UUID] = owner()
    label: Mapped[str | None] = mapped_column(String(80))
    api_id: Mapped[int | None] = mapped_column(Integer)  # None: the platform's app
    api_hash_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    session_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    login_state_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="code_sent")
    status_detail: Mapped[str | None] = mapped_column(String(300))
    flood_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account_name: Mapped[str | None] = mapped_column(String(120))  # the Telegram account that logged in
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    chat_title: Mapped[str | None] = mapped_column(String(200))
    chat_kind: Mapped[str | None] = mapped_column(String(10))  # channel | group
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[str] = mapped_column(String(40), default="vip_setups", server_default="vip_setups")
    # the reader (python -m ae_signals): listening | connecting | error | off, and the newest message it stored
    reader_state: Mapped[str] = mapped_column(String(20), default="off", server_default="off")
    reader_detail: Mapped[str | None] = mapped_column(String(300))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignalMessage(Base):
    """A message of a source's chat exactly as received (never modified; an edit replaces the text and sets
    edit_date), so history can always be re-read with a better parser."""

    __tablename__ = "signal_messages"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = owner()
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("signal_sources.id", ondelete="CASCADE"))
    msg_id: Mapped[int] = mapped_column(BigInteger)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[int | None] = mapped_column(BigInteger)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("source_id", "msg_id"),
        Index("ix_signal_messages_source_date", "source_id", "date"),
    )


class SignalRow(Base):
    """A signal assembled from a source's messages (ae_core.signals): the tip, its levels and how it went. Rebuilt
    from signal_messages whenever they change; id = the header message's id within the source."""

    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = owner()
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("signal_sources.id", ondelete="CASCADE"))
    header_msg_id: Mapped[int] = mapped_column(BigInteger)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    index: Mapped[str] = mapped_column(String(20))
    strike: Mapped[int] = mapped_column(Integer)
    option_type: Mapped[str] = mapped_column(String(2))
    action: Mapped[str] = mapped_column(String(4))
    direction: Mapped[str] = mapped_column(String(8))
    entry_low: Mapped[float] = mapped_column(Float)
    entry_high: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[list[float]] = mapped_column(JSONB, default=list)
    targets_done: Mapped[list[int]] = mapped_column(JSONB, default=list)
    rationale: Mapped[str | None] = mapped_column(String(300))
    valid_for: Mapped[str | None] = mapped_column(String(100))
    intraday: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(10), default="OPEN")
    last_price: Mapped[float | None] = mapped_column(Float)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    message_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        UniqueConstraint("source_id", "header_msg_id"),
        Index("ix_signals_source_date", "source_id", "date"),
    )


class SignalOverride(Base):
    """A user's correction of how a message was read (its kind); wins over the parser and is a test case for it."""

    __tablename__ = "signal_overrides"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = owner()
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("signal_sources.id", ondelete="CASCADE"))
    msg_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("source_id", "msg_id"),)


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
    kind: Mapped[str] = mapped_column(String(40), default="time_based")  # mirrors config["kind"]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # ae_core.strategy (ADR 0010)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # config's shape
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every config change
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
    # phase 9: what ran and how it is doing
    strategy_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(40), default="time_based", server_default="time_based")
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    multiplier: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # lots x this
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")  # the runner's state
    unrealized_pnl: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0), server_default="0")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # live: log, never send


class UserRiskSettings(UUIDPk, Timestamps, Base):
    """A user's own trading limits across all their runs (None = no limit). Checked by the engine before every
    entry; breaching the daily loss/profit squares everything off."""

    __tablename__ = "user_risk_settings"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    max_daily_loss: Mapped[Decimal | None] = mapped_column(AMOUNT)
    max_daily_profit: Mapped[Decimal | None] = mapped_column(AMOUNT)
    max_open_positions: Mapped[int | None] = mapped_column(Integer)
    max_trades_per_day: Mapped[int | None] = mapped_column(Integer)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)


class HistoryCandle(Base):
    """One stored 1-minute candle for backtesting: an index (key "NIFTY") or an option contract (key
    "NIFTY:2026-10-06:25000:CE", the price feed's key). Platform data: only the system reads and writes it."""

    __tablename__ = "history_candles"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)  # the minute's start
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    oi: Mapped[int | None] = mapped_column(BigInteger)  # open interest at the minute's close (options, when known)


class BacktestRun(UUIDPk, Timestamps, Base):
    """A backtest a user asked for: the strategy's config as it was, the range, and (when done) the result."""

    __tablename__ = "backtest_runs"
    user_id: Mapped[uuid.UUID] = owner()
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("strategies.id", ondelete="SET NULL"))
    strategy_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    multiplier: Mapped[int] = mapped_column(Integer, default=1)
    slippage_pct: Mapped[float] = mapped_column(Float, default=0.05)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | done | error
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_backtest_runs_user_created", "user_id", "created_at"),)


class NotificationSettings(UUIDPk, Timestamps, Base):
    """How and about what a user wants to be told. events None = the defaults (ae_core.notifications)."""

    __tablename__ = "notification_settings"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_address: Mapped[str | None] = mapped_column(String(320))  # None: the account's email
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40))
    telegram_link_code: Mapped[str | None] = mapped_column(String(40))  # sent to the bot as /start <code> to link
    events: Mapped[list[str] | None] = mapped_column(JSONB)


class Notification(UUIDPk, Base):
    """The outbox: queued by the engine, sent by the worker, kept as the user's history."""

    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID] = owner()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | sent | failed | skipped
    sent_via: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_notifications_pending", "status", "created_at"),)


class PlatformSetting(UUIDPk, Timestamps, Base):
    """Small platform switches set by admins (e.g. trading_halted). System only."""

    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(60), unique=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


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
