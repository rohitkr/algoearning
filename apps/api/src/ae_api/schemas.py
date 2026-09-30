"""Response/request models (these become the web app's TypeScript types via OpenAPI)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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
    features: dict[str, FeatureValue]
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
    config: dict[str, Any] = Field(default_factory=dict)


class StrategyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    config: dict[str, Any] | None = None
    status: Literal["draft", "ready", "archived"] | None = None


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str | None
    kind: str
    config: dict[str, Any]
    version: int
    status: Literal["draft", "ready", "archived"]
    created_at: datetime
    updated_at: datetime


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
