"""Response/request models (these become the web app's TypeScript types via OpenAPI)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from ae_core.strategy import StrategyConfig, StrategyKind, default_config
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ServerIpOut(BaseModel):
    """The IP this server reaches brokers from (checked by the worker): what each user registers in their Kite app."""

    ip: str | None
    previous: str | None = None
    changed_at: datetime | None = None
    checked_at: datetime | None = None
    changed_recently: bool = False  # changed in the last 3 days: the dashboard warns Zerodha users


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


# -- signal sources (ADR 0025) ----------------------------------------------------------------------------------
SignalSourceStatus = Literal[
    "code_sent", "password_needed", "connected", "flood_wait", "needs_reconnect", "disconnected"
]


class SignalSourceOut(BaseModel):
    """Never contains a secret: the phone masked, the API hash and the Telegram session not at all."""

    id: uuid.UUID
    label: str | None
    status: SignalSourceStatus
    status_detail: str | None
    next_step: Literal["code", "password", "chat", "login"] | None  # what the user does next, if anything
    flood_until: datetime | None
    phone_masked: str | None
    api_id: int | None
    platform_app: bool  # the platform's Telegram app, not the user's own
    account_name: str | None
    chat_id: int | None
    chat_title: str | None
    chat_kind: str | None
    connected_at: datetime | None
    created_at: datetime
    reader_state: Literal["listening", "connecting", "error", "off"] = "off"
    reader_detail: str | None = None
    last_message_at: datetime | None = None


SignalKind = Literal["SIGNAL", "DETAILS", "TARGET", "SL_HIT", "TICK", "MEDIA", "ADVISORY", "NOISE", "UNCLEAR"]


class SignalOut(BaseModel):
    """A signal as the channel gave it (the tip's own levels) and how it went."""

    id: int  # the header message's id in the chat
    date: datetime
    index: str
    strike: int
    option_type: str
    action: str
    direction: Literal["BULLISH", "BEARISH"]
    entry_low: float
    entry_high: float
    stop_loss: float | None
    targets: list[float]
    targets_done: list[int]
    rationale: str | None
    valid_for: str | None
    intraday: bool
    status: Literal["OPEN", "T1", "T2", "T3", "SL_HIT"]
    last_price: float | None
    complete: bool
    message_ids: list[int]


class TipFillOut(BaseModel):
    time: datetime
    price: float
    qty: int
    reason: str  # TARGET 1..3 / STOP LOSS / END OF DAY


class TipTradeOut(BaseModel):
    """One tip replayed exactly as given (ADR 0025): what the tip said, what the price did, what the follower got."""

    signal_id: int
    date: datetime
    tip: str  # "BUY NIFTY 22450 CE"
    direction: Literal["BULLISH", "BEARISH"]
    entry_low: float
    entry_high: float
    stop_loss: float | None
    targets: list[float]
    channel_status: Literal["OPEN", "T1", "T2", "T3", "SL_HIT"]  # what the channel itself reported
    entry: Literal["IN_ZONE", "CHASED", "NOT_PLACED", "NO_DATA"]
    note: str
    expiry: date | None  # inferred: the tip never names one
    price_at_signal: float | None
    above_zone: float | None  # premium points over the top of the range
    buffer: float | None
    entry_time: datetime | None
    entry_price: float | None
    qty: int
    exits: list[TipFillOut]
    peak_price: float | None  # highest price in the minutes before the exit minute
    peak_points: float | None  # ... over our entry: did the market move our way first?
    peak_time: datetime | None
    stopped: bool  # closed by a stop (the tip's, at cost, or trailing)
    breakeven_time: datetime | None  # when the stop moved to cost; None = the price never got that far
    final_stop: float | None
    trail_moves: int
    gross: float
    charges: float
    net: float


class TipReplayOut(BaseModel):
    start: date
    end: date
    lots: int
    lot_sizes: dict[str, int]
    buffers: dict[str, float]
    breakevens: dict[str, float]
    trails: dict[str, float]
    slippage_pct: float
    summary: dict[str, Any]
    daily: list[dict[str, Any]]
    trades: list[TipTradeOut]  # newest first
    warnings: list[str]


class SignalMessageOut(BaseModel):
    """A chat message exactly as received, and how it was read."""

    msg_id: int
    date: datetime
    edit_date: datetime | None
    text: str
    reply_to: int | None
    has_media: bool
    kind: SignalKind
    data: dict[str, Any]
    signal_id: int | None
    overridden: bool


class SignalKindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SignalKind


class SignalSourceLoginIn(BaseModel):
    """Start (or restart) the Telegram login. Without api_id / api_hash the platform's app is used; on an existing
    source an empty phone means the one stored."""

    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9 ()-]{7,20}$")
    api_id: int | None = Field(default=None, gt=0, lt=2**31)
    api_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{32}$")


class SignalSourceCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9 -]{3,12}$")


class SignalSourcePasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class SignalSourceChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: int


class TelegramChatOut(BaseModel):
    id: int
    title: str
    kind: Literal["channel", "group"]
    username: str | None


class SignalSetupOut(BaseModel):
    platform_app: bool  # the platform offers its own Telegram app (no my.telegram.org step for the user)
    max_sources: int | None
    used: int


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
    live_unlocked: bool
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
    freeze_qty: int  # the exchange's max units per order (orders are sliced to it)
    source: str
    updated_at: datetime


class InstrumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_open: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    session_close: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    is_active: bool | None = None
    freeze_qty: int | None = Field(default=None, ge=1, le=100_000)


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


# -- market data -----------------------------------------------------------------------------------------------
class Quote(BaseModel):
    key: str  # "NIFTY" or "NIFTY:2026-10-06:25000:CE"
    ltp: float
    prev_close: float | None
    ts: datetime


class MarketSnapshot(BaseModel):
    quotes: list[Quote]
    feed_status: Literal["live", "simulated", "down"]  # down: no price in the last 2 minutes


class FeedInstrument(BaseModel):
    code: str
    ltp: float | None
    ts: datetime | None
    bars_today: int


class FeedLogin(BaseModel):
    """A price provider whose keys are set, and its daily admin login."""

    provider: Literal["breeze", "kite"]
    name: str
    login_url: str
    session_expires_at: datetime | None  # null or past: log in today
    account: str | None = None  # the account today's session belongs to (Kite: the Zerodha client ID)
    expected_account: str | None = None  # Kite: KITE_FEED_CLIENT_ID, the only account accepted (null: any)


class MarketDataAdmin(BaseModel):
    source: str | None  # breeze | kite | simulated | None (feed not running)
    connected: bool
    session: str | None  # active | login needed | not needed
    logins: list[FeedLogin]  # the providers that are set up (none: the feed can only simulate)
    error: str | None
    wanted: int
    subscribed: int
    api_calls_today: int
    last_event: datetime | None
    updated_at: datetime | None  # the feed's last heartbeat
    instruments: list[FeedInstrument]


class BreezeSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_token: str = Field(min_length=4, max_length=2000)  # apisession from ICICI's redirect, or that whole URL


class KiteSessionIn(BaseModel):
    """One of: the request token from Kite's redirect (or the whole URL it landed on), exchanged here for the day's
    session; or an access token another program already got from a login to the same Kite app."""

    model_config = ConfigDict(extra="forbid")
    request_token: str | None = Field(default=None, min_length=4, max_length=2000)
    access_token: str | None = Field(default=None, min_length=4, max_length=200)

    @model_validator(mode="after")
    def _one_token(self) -> KiteSessionIn:
        if (self.request_token is None) == (self.access_token is None):
            raise ValueError("send either request_token or access_token")
        return self


# -- runs (the engine) -----------------------------------------------------------------------------------------
RunStatusT = Literal["pending", "running", "stopping", "stopped", "completed", "error"]


class DeployIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["paper", "live"] = "paper"
    broker_account_id: uuid.UUID | None = None  # required for live; optional on paper (for the record)
    multiplier: int = Field(default=1, ge=1, le=100)  # every leg's lots x this
    dry_run: bool = False  # live only: log every order, send nothing
    confirm: str | None = Field(default=None, max_length=120)  # live (not dry run): the strategy's name, typed


class LiveBroker(BaseModel):
    id: uuid.UUID
    client_id: str
    label: str | None
    connected: bool  # logged in today
    engine_enabled: bool  # the account's Trading Engine switch


class LiveStatus(BaseModel):
    """Whether the signed-in user can trade live, and if not, why (shown in the deploy dialog)."""

    plan_allows: bool
    unlocked: bool  # an admin allowed real orders for this user
    brokers: list[LiveBroker]
    can_dry_run: bool
    can_go_live: bool
    reasons: list[str]


class RunOut(BaseModel):
    dry_run: bool = False
    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_name: str
    kind: str
    underlying: str
    mode: Literal["paper", "live"]
    status: RunStatusT
    multiplier: int
    broker_account_id: uuid.UUID | None
    realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    created_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None
    heartbeat_at: datetime | None  # the engine's last step for this run
    engine_stale: bool  # running, but the engine has not stepped it for 30 seconds
    stop_reason: str | None
    error: str | None


class PositionOut(BaseModel):
    id: uuid.UUID
    leg: str | None
    tradingsymbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: str
    side: Literal["BUY", "SELL"]
    lots: int
    quantity: int
    status: str  # open | closed
    entry_price: float
    entry_time: datetime | None
    last_ltp: float | None
    current_sl: float | None
    target: float | None
    exit_price: float | None
    exit_time: datetime | None
    exit_reason: str | None
    pnl: float


class OrderOut(BaseModel):
    id: uuid.UUID
    trade_id: uuid.UUID
    kind: str
    side: Literal["BUY", "SELL"]
    quantity: int
    avg_price: float | None
    status: str
    created_at: datetime


class RunEvent(BaseModel):
    id: int
    ts: datetime
    event: str
    level: str
    detail: dict[str, Any]


class RunDetail(BaseModel):
    run: RunOut
    positions: list[PositionOut]
    orders: list[OrderOut]
    events: list[RunEvent]


class RiskSettingsIO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_daily_loss: float | None = Field(default=None, gt=0, le=1_000_000_000)
    max_daily_profit: float | None = Field(default=None, gt=0, le=1_000_000_000)
    max_open_positions: int | None = Field(default=None, ge=0, le=1000)
    max_trades_per_day: int | None = Field(default=None, ge=0, le=10000)
    kill_switch: bool = False


class AdminRunOut(RunOut):
    user_id: uuid.UUID
    user_email: str


class TradingHaltIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    halted: bool
    reason: str | None = Field(default=None, max_length=300)


class EngineStatus(BaseModel):
    trading_halted: bool
    halt_reason: str | None
    active_runs: int
    last_heartbeat: datetime | None  # the most recent step of any run


# -- reports ---------------------------------------------------------------------------------------------------
class ReportDay(BaseModel):
    day: date
    pnl: float
    trades: int
    cumulative: float


class ReportSummary(BaseModel):
    from_date: date
    to_date: date
    total_pnl: float
    trades: int
    wins: int
    losses: int
    win_rate: float | None  # 0..1
    avg_win: float | None
    avg_loss: float | None
    profit_factor: float | None
    max_drawdown: float
    best_day: ReportDay | None
    worst_day: ReportDay | None
    trading_days: int
    expectancy: float | None = None  # average P&L per trade
    max_consecutive_losses: int = 0


class StrategyPerformance(BaseModel):
    strategy_id: uuid.UUID | None
    strategy_name: str
    runs: int
    trades: int
    pnl: float
    win_rate: float | None


class TradeRow(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    strategy_name: str
    mode: Literal["paper", "live"]
    underlying: str
    tradingsymbol: str
    expiry: date
    strike: float
    option_type: str
    side: Literal["BUY", "SELL"]
    quantity: int
    entry_time: datetime | None
    entry_price: float
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str | None
    pnl: float


class OpenPosition(TradeRow):
    last_ltp: float | None
    current_sl: float | None
    target: float | None


class LiveUnlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unlocked: bool


# -- notifications ---------------------------------------------------------------------------------------------
class NotificationEventInfo(BaseModel):
    key: str
    label: str
    hint: str
    default: bool


class NotificationSettingsOut(BaseModel):
    email_enabled: bool
    email_address: str | None  # None: the account's email
    account_email: str
    telegram_enabled: bool
    telegram_connected: bool
    events: list[str]  # the events switched on (defaults when never chosen)
    catalog: list[NotificationEventInfo]
    email_available: bool  # the server can send email
    telegram_available: bool  # the server has a Telegram bot


class NotificationSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email_enabled: bool
    email_address: str | None = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    telegram_enabled: bool
    events: list[str]


class TelegramLink(BaseModel):
    url: str | None  # https://t.me/<bot>?start=<code>
    code: str
    bot_username: str | None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    event: str
    title: str
    body: str
    status: str
    sent_via: list[str]
    error: str | None


class PreflightCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class PreflightOut(BaseModel):
    ok: bool
    checks: list[PreflightCheck]


# -- backtesting -----------------------------------------------------------------------------------------------
class BacktestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: uuid.UUID
    start_date: date
    end_date: date
    multiplier: int = Field(default=1, ge=1, le=100)
    slippage_pct: float = Field(default=0.05, ge=0, le=5)


class BacktestOut(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID | None
    strategy_name: str
    kind: str
    underlying: str
    start_date: date
    end_date: date
    multiplier: int
    slippage_pct: float
    status: Literal["pending", "running", "done", "error"]
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    net_pnl: float | None  # from the result, for the list
    trades: int | None


class BacktestDetail(BacktestOut):
    result: dict[str, Any] | None  # summary, daily, trades, warnings (see ae_core.backtest.summarize_result)


class HistoryCoverage(BaseModel):
    underlying: str
    index_from: date | None
    index_to: date | None
    index_days: int
    option_from: date | None
    option_to: date | None
    option_days: int
    expiries: int


# -- charts (live SMC charts, ae_api.charts) ---------------------------------------------------------------------
class ChartInstrumentOut(BaseModel):
    code: str
    name: str


class ChartOptions(BaseModel):
    instruments: list[ChartInstrumentOut]
    timeframes: list[int]  # minutes


class ChartCandle(BaseModel):
    time: int  # epoch seconds of the period's start
    open: float
    high: float
    low: float
    close: float
    volume: float


class SmcBox(BaseModel):
    kind: Literal["fvg", "ob"]
    side: Literal["bull", "bear"]
    top: float
    bottom: float
    start: int  # epoch seconds
    end: int | None  # the mitigating candle; None: still open
    label: str


class SmcLine(BaseModel):
    kind: Literal["bos", "choch", "liquidity"]
    side: Literal["bull", "bear"]
    price: float
    start: int
    end: int | None  # the breaking / sweeping candle; None: not taken yet
    label: str


class SmcLevel(BaseModel):
    kind: Literal["pdh", "pdl", "resistance", "support"]
    price: float
    label: str


class SmcOverlay(BaseModel):
    boxes: list[SmcBox]
    lines: list[SmcLine]
    levels: list[SmcLevel]
    candles: int
    swing_length: int


class ChartSnapshot(BaseModel):
    """Everything a chart needs; the stream (/v1/charts/stream) starts with this and then sends changes."""

    type: Literal["snapshot"] = "snapshot"
    key: str
    name: str
    timeframe: int
    candles: list[ChartCandle]  # closed, oldest first
    forming: ChartCandle | None
    smc: SmcOverlay
    status: Literal["live", "simulated", "down"]
    last_price: float | None
    error: str | None
