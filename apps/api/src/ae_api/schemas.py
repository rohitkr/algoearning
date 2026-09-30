"""Response/request models (these become the web app's TypeScript types via OpenAPI)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from ae_core.strategy import StrategyConfig, StrategyKind, default_config
from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Every non-2xx response has this shape (see errors.py)."""

    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse, "description": desc}
    for code, desc in (
        (400, "Bad request"),
        (401, "Sign in required"),
        (403, "Forbidden"),
        (404, "Not found"),
        (422, "Validation error"),
        (503, "Unavailable"),
    )
}


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class Me(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    role: Literal["user", "admin"]
    created_at: datetime


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    price_paise: int
    currency: str
    interval: Literal["month", "year"]
    features: dict[str, Any]


FeatureValue = bool | int | None


class FeatureInfo(BaseModel):
    key: str
    kind: Literal["flag", "limit"]
    label: str


class UsageItem(BaseModel):
    used: int
    limit: int | None  # None: unlimited


class EntitlementsOut(BaseModel):
    plan_code: str
    plan_name: str
    subscription_status: str | None
    current_period_end: datetime | None
    features: dict[str, FeatureValue]  # the plan's, with `overrides` applied
    overrides: dict[str, FeatureValue]  # set for this user by an admin
    usage: dict[str, UsageItem]
    catalog: list[FeatureInfo]


class PlanAdminIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=1, max_length=80)
    price_paise: int = Field(ge=0, le=100_000_000)
    interval: Literal["month", "year"] = "month"
    features: dict[str, FeatureValue]
    sort_order: int = 0
    is_active: bool = True


class PlanAdminPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    price_paise: int | None = Field(default=None, ge=0, le=100_000_000)
    features: dict[str, FeatureValue] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class PlanAdminOut(PlanOut):
    is_active: bool
    sort_order: int


class StrategyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    config: StrategyConfig = Field(default_factory=default_config)


class StrategyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    config: StrategyConfig | None = None
    status: Literal["draft", "ready", "archived"] | None = None


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    kind: StrategyKind
    config: StrategyConfig
    schema_version: int
    version: int
    status: Literal["draft", "ready", "archived"]
    created_at: datetime
    updated_at: datetime


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    exchange: str
    lot_size: int  # of the nearest expiry, refreshed daily
    strike_step: int
    weekly_expiry: bool
    session_open: str  # trading hours, HH:MM IST
    session_close: str
    refreshed_at: datetime | None  # last refresh from the broker's instrument list (null: seed values)


class PresetOut(BaseModel):
    id: str
    name: str
    description: str
    config: StrategyConfig


class StrategyLimits(BaseModel):
    max_legs: int
    max_strike_offset: int
    max_lots: int
    max_lots_per_order: int | None  # the user's plan (null = unlimited)


class StrategyCatalogOut(BaseModel):
    """Everything the builder needs: instruments, starting points and limits."""

    instruments: list[InstrumentOut]
    presets: list[PresetOut]
    limits: StrategyLimits


class ValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]  # raw on purpose: half-typed builder state must still get field-level errors


class ConfigIssue(BaseModel):
    loc: list[str | int]  # path inside the config, e.g. ["legs", 0, "stop_loss", "value"]
    msg: str
    type: str


class ValidateOut(BaseModel):
    valid: bool  # true: the config can be saved (warnings do not block saving)
    errors: list[ConfigIssue]
    warnings: list[ConfigIssue]


class DuplicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(min_length=1, max_length=40)


class CheckoutOut(BaseModel):
    """What the browser needs to open Razorpay Checkout (key_id is public by design)."""

    provider: Literal["razorpay"]
    key_id: str
    order_id: str
    amount_paise: int
    currency: str
    plan_code: str
    plan_name: str
    customer_name: str | None
    customer_email: str
    period_end: datetime  # when the plan would run until if paid now
    credit_paise: int  # unused value of the current plan carried over


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=60)
    payment_id: str = Field(min_length=1, max_length=60)
    signature: str = Field(min_length=1, max_length=128)


class PurchaseOut(BaseModel):
    outcome: Literal["paid", "already_paid"]
    plan_code: str
    period_end: datetime | None


class PaymentOut(BaseModel):
    order_id: str
    payment_id: str | None
    plan_code: str
    plan_name: str
    amount_paise: int
    currency: str
    status: Literal["created", "paid", "failed"]
    paid_at: datetime | None
    created_at: datetime


class BrokerInfoOut(BaseModel):
    code: str
    name: str
    available: bool
    developer_console: str
    notes: str
    redirect_url: str | None  # what to register as the Redirect URL in the broker's developer console


class BrokerAccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: Literal["zerodha", "upstox", "angelone"]
    client_id: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    label: str | None = Field(default=None, max_length=80)
    api_key: str = Field(min_length=4, max_length=200)
    api_secret: str = Field(min_length=4, max_length=200, repr=False)


class BrokerAccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    api_key: str | None = Field(default=None, min_length=4, max_length=200)
    api_secret: str | None = Field(default=None, min_length=4, max_length=200, repr=False)
    terminal_enabled: bool | None = None
    engine_enabled: bool | None = None


class BrokerAccountOut(BaseModel):
    """Never contains a secret: the API key only masked, the secret and access token not at all."""

    id: uuid.UUID
    broker: str
    broker_name: str
    client_id: str
    label: str | None
    api_key_masked: str
    static_ip: str | None
    status: Literal["connected", "expired", "disconnected", "error"]
    session_expires_at: datetime | None
    last_login_at: datetime | None
    terminal_enabled: bool
    engine_enabled: bool
    created_at: datetime


class BrokerLoginOut(BaseModel):
    login_url: str


class BrokerTestOut(BaseModel):
    ok: bool
    client_id: str | None = None
    name: str | None = None
    message: str | None = None


# -- Monitor (admin) -------------------------------------------------------------------------------------------
class AdminUserRow(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    avatar_url: str | None
    role: Literal["user", "admin"]
    status: Literal["active", "suspended", "deleted"]
    plan_code: str  # the plan that applies now
    plan_name: str
    has_overrides: bool
    strategies: int
    broker_accounts: int
    created_at: datetime
    last_seen_at: datetime | None


class AdminSubscription(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    plan_code: str
    plan_name: str
    status: str
    provider: str  # razorpay | manual (granted by an admin)
    current_period_start: datetime | None
    current_period_end: datetime | None
    created_at: datetime


class AdminBrokerAccount(BaseModel):
    id: uuid.UUID
    broker: str
    client_id: str
    label: str | None
    status: str
    engine_enabled: bool
    last_login_at: datetime | None


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    user_id: uuid.UUID | None
    user_email: str | None = None
    actor: str
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    detail: dict[str, Any]

    @field_validator("ip", mode="before")
    @classmethod
    def _ip(cls, v: object) -> str | None:  # INET comes back as an ipaddress object
        return None if v is None else str(v)


class AdminUserDetail(BaseModel):
    user: AdminUserRow
    entitlements: EntitlementsOut
    override_note: str | None
    subscriptions: list[AdminSubscription]
    broker_accounts: list[AdminBrokerAccount]
    recent_activity: list[AuditEntry]


class AdminUserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "suspended"] | None = None
    role: Literal["user", "admin"] | None = None


class OverridesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: dict[str, FeatureValue]  # only the features to override; {} removes every override
    note: str | None = Field(default=None, max_length=500)


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(min_length=1, max_length=40)
    days: int = Field(ge=1, le=3660)


class InstrumentAdminOut(InstrumentOut):
    is_active: bool
    source: str
    updated_at: datetime


class InstrumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_open: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    session_close: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    is_active: bool | None = None


class InstrumentRefreshOut(BaseModel):
    changed: dict[str, dict[str, list[Any]]]  # code -> field -> [old, new]
    unchanged: list[str]
    missing: list[str]


class RecentPayment(BaseModel):
    order_id: str
    user_email: str
    plan_name: str
    amount_paise: int
    paid_at: datetime | None


class Overview(BaseModel):
    users: int
    active_users: int  # seen in the last 7 days
    new_users_7d: int
    suspended_users: int
    paying_users: int  # on a paid plan right now (paid or granted)
    strategies: int
    ready_strategies: int
    broker_accounts: int
    connected_broker_accounts: int
    revenue_30d_paise: int
    recent_payments: list[RecentPayment]
    recent_users: list[AdminUserRow]
    instruments_refreshed_at: datetime | None
