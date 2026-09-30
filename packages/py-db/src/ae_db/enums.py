"""Enumerations stored as VARCHAR + CHECK constraint (not native PG enums: adding a value is then a plain
migration instead of an ALTER TYPE)."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class PlanInterval(StrEnum):
    MONTH = "month"
    YEAR = "year"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"  # created, first payment not yet confirmed
    PAST_DUE = "past_due"  # renewal failed, in grace period
    CANCELLED = "cancelled"  # will not renew; access until current_period_end
    EXPIRED = "expired"


class Broker(StrEnum):
    ZERODHA = "zerodha"
    UPSTOX = "upstox"
    ANGELONE = "angelone"


class BrokerAccountStatus(StrEnum):
    DISCONNECTED = "disconnected"  # credentials saved, never logged in / logged out
    CONNECTED = "connected"  # valid access token
    EXPIRED = "expired"  # daily token expired: log in again
    ERROR = "error"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderKind(StrEnum):
    ENTRY = "ENTRY"
    SL = "SL"
    TARGET = "TARGET"
    PARTIAL = "PARTIAL"
    EXIT = "EXIT"


class BillingKind(StrEnum):
    PREPAID = "prepaid"  # paid upfront for a period; renewed by paying again
    RECURRING = "recurring"  # provider auto-renews (Razorpay Subscriptions, later)


class BillingOrderStatus(StrEnum):
    CREATED = "created"  # checkout opened, not paid yet
    PAID = "paid"  # payment captured and applied to the subscription
    FAILED = "failed"
