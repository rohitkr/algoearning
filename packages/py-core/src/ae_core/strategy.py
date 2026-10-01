"""What a saved strategy means. The API validates every save with this module and the trading engine (phase 9)
reads configs with it, so the two can never disagree about a field (ADR 0010).

A config is one of three kinds, told apart by `kind`:

    time_based      the builder: 1-6 option legs entered at a fixed time on chosen weekdays, each with its own
                    strike rule, stop-loss, target, trailing stop and re-entry, plus strategy-wide MTM limits
    range_breakout  the proven positional 2h range breakout seller (algo-trading-claude, RangeBreakoutParams)
    zero_dte        the proven expiry-day ITM straddle seller (algo-trading-claude, ZeroDteParams)
    smc_scalp       intraday options buyer on Smart Money Concepts read from the index (ADR 0018)

Validation has two layers: the Pydantic models check types and bounds; `check()` checks rules that span
fields (entry before exit, weekly expiry only where the exchange lists one, trailing needs a stop-loss, ...).
`plan_warnings()` compares a config with the user's plan without refusing it: the engine enforces plan limits
when the strategy is deployed.

Configs belong to the user who saved them. Instruments (lot size, strike step, expiry type, trading hours) are
platform data set by the exchange: they live in the database, refreshed daily from the broker's instrument list
(ADR 0011), and are passed in. DEFAULT_INSTRUMENTS only seeds that table and serves tests.

Changing the shape of a config: bump SCHEMA_VERSION and teach `migrate()` to upgrade older configs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

SCHEMA_VERSION = 1
MAX_LEGS = 6
MAX_STRIKE_OFFSET = 20  # strikes away from ATM
MAX_LOTS = 100  # sanity cap per leg; plans usually allow far fewer (max_lots_per_order)


# -- instruments (platform data: exchange facts, stored in the database) ---------------------------------------
@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    exchange: str  # derivatives segment: NFO (NSE) or BFO (BSE)
    lot_size: int  # of the nearest expiry; the engine uses each contract's own lot size when it orders
    strike_step: int
    weekly_expiry: bool  # False: only monthly expiries are listed
    session_open: str = "09:15"  # when strategies may trade (HH:MM, IST)
    session_close: str = "15:40"


Instruments = Mapping[str, Instrument]

# Seed values for the instruments table (migration 0005); the daily refresh keeps the table current.
DEFAULT_INSTRUMENTS: dict[str, Instrument] = {
    i.code: i
    for i in (
        Instrument("NIFTY", "Nifty 50", "NFO", 65, 50, True),
        Instrument("BANKNIFTY", "Nifty Bank", "NFO", 30, 100, False),
        Instrument("FINNIFTY", "Nifty Financial Services", "NFO", 60, 50, False),
        Instrument("MIDCPNIFTY", "Nifty Midcap Select", "NFO", 120, 25, False),
        Instrument("SENSEX", "BSE Sensex", "BFO", 20, 100, True),
    )
}

Underlying = Literal["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]
Weekday = Literal["MON", "TUE", "WED", "THU", "FRI"]
WEEKDAYS: tuple[Weekday, ...] = ("MON", "TUE", "WED", "THU", "FRI")
HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]  # "09:20"; compares as text
Expiry = Literal["current_week", "next_week", "current_month", "next_month"]
WEEKLY_EXPIRIES = ("current_week", "next_week")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo such as `stoploss` must fail, not be silently ignored


# -- time_based ------------------------------------------------------------------------------------------------
class Threshold(_Model):
    """A distance from the entry price: stop-loss or target."""

    unit: Literal["points", "percent"] = "percent"
    value: float = Field(gt=0, le=100_000)
    basis: Literal["premium", "underlying"] = "premium"  # measured on the option's price or on the index


class Trailing(_Model):
    """Each time the price moves `trigger` in the leg's favour, move the stop-loss `step` the same way."""

    unit: Literal["points", "percent"] = "points"
    trigger: float = Field(gt=0, le=100_000)
    step: float = Field(gt=0, le=100_000)


class ReEntry(_Model):
    mode: Literal["at_cost", "immediate"] = "at_cost"  # at_cost: wait for the price to return to the entry price
    count: int = Field(default=1, ge=1, le=5)


class Strike(_Model):
    """atm: ATM +/- `offset` strikes (positive = out of the money, negative = in the money).
    premium: the strike whose premium is closest to `premium` at entry."""

    mode: Literal["atm", "premium"] = "atm"
    offset: int = Field(default=0, ge=-MAX_STRIKE_OFFSET, le=MAX_STRIKE_OFFSET)
    premium: float | None = Field(default=None, gt=0, le=100_000)


class Leg(_Model):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,12}$")]
    action: Literal["BUY", "SELL"]
    option_type: Literal["CE", "PE"]
    lots: int = Field(default=1, ge=1, le=MAX_LOTS)
    expiry: Expiry = "current_week"
    strike: Strike = Field(default_factory=Strike)
    stop_loss: Threshold | None = None
    target: Threshold | None = None
    trailing: Trailing | None = None
    reentry_on_sl: ReEntry | None = None
    reentry_on_target: ReEntry | None = None


class Timing(_Model):
    entry: HHMM = "09:20"
    exit: HHMM = "15:15"
    days: list[Weekday] = Field(default_factory=lambda: list(WEEKDAYS), min_length=1, max_length=5)


class StrategyRisk(_Model):
    """Limits on the whole strategy's running profit or loss (MTM), in rupees."""

    mtm_stop_loss: float | None = Field(default=None, gt=0, le=100_000_000)
    mtm_target: float | None = Field(default=None, gt=0, le=100_000_000)
    exit_all_on_leg_sl: bool = False  # when any leg's stop-loss hits, exit every other leg too


class TimeBasedConfig(_Model):
    kind: Literal["time_based"] = "time_based"
    underlying: Underlying = "NIFTY"
    timing: Timing = Field(default_factory=Timing)
    legs: list[Leg] = Field(min_length=1, max_length=MAX_LEGS)
    risk: StrategyRisk = Field(default_factory=StrategyRisk)


# -- the two proven strategies from algo-trading-claude --------------------------------------------------------
class RangeBreakoutConfig(_Model):
    """Positional 2h range breakout seller: the first close outside the range_start..range_end high/low sells an
    ITM option (upside breakout sells a PUT, downside a CALL) on the weekly expiry after the entry day. Stop-loss
    is on the index (stop_loss_pct against the entry); one re-entry at cost; exit at exit_time on expiry day, or
    on the entry day when intraday_only. hedge_width buys a wing that many points further out (None = naked)."""

    kind: Literal["range_breakout"] = "range_breakout"
    underlying: Underlying = "NIFTY"
    range_start: HHMM = "09:15"
    range_end: HHMM = "11:15"
    last_entry: HHMM = "15:00"
    exit_time: HHMM = "15:15"
    itm_points: int = Field(default=100, ge=0, le=2000)
    stop_loss_pct: float = Field(default=0.5, gt=0, le=10)
    reentry: bool = True
    lots: int = Field(default=1, ge=1, le=MAX_LOTS)
    expiry_offset: int = Field(default=0, ge=0, le=3)
    intraday_only: bool = False
    hedge_width: int | None = Field(default=300, gt=0, le=5000)


class ZeroDteConfig(_Model):
    """Expiry-day ITM straddle seller: on each expiry day, sells a CALL at ATM - itm_points and a PUT at
    ATM + itm_points at the entry time that did best over the last `lookback` expiry days (candidates every
    step_minutes from first_entry to last_entry). Stop-loss stop_loss_pct on each leg's premium, one re-entry,
    exit at exit_time."""

    kind: Literal["zero_dte"] = "zero_dte"
    underlying: Underlying = "NIFTY"
    first_entry: HHMM = "09:20"
    last_entry: HHMM = "14:30"
    step_minutes: int = Field(default=10, ge=5, le=60)
    exit_time: HHMM = "15:15"
    stop_loss_pct: float = Field(default=30.0, gt=0, le=500)
    reentry: bool = True
    lookback: int = Field(default=8, ge=2, le=26)
    lots: int = Field(default=1, ge=1, le=MAX_LOTS)
    itm_points: int = Field(default=100, ge=0, le=2000)
    hedge_width: int | None = Field(default=None, gt=0, le=5000)


# -- SMC options scalping (ADR 0018) ------------------------------------------------------------------------------
class SmcTimeframes(_Model):
    """Minutes per candle: bias (structure, premium/discount), setup (sweep, displacement, BOS/CHoCH, OB/FVG) and
    entry (the tap and its confirmation). All are built from the index's 1-minute candles."""

    bias: Literal[15, 30, 60] = 15
    setup: Literal[3, 5, 10] = 5
    entry: Literal[1, 2, 3] = 1


class SmcRules(_Model):
    """The thresholds that make each concept objective (see ae_core.trading.smc)."""

    swing_bias: int = Field(default=2, ge=1, le=5)  # candles each side of a swing, bias timeframe
    swing_setup: int = Field(default=2, ge=1, le=5)
    swing_entry: int = Field(default=2, ge=1, le=5)
    bias_min_breaks: int = Field(default=2, ge=1, le=4)  # consecutive breaks needed for a clear bias
    atr_period: int = Field(default=14, ge=5, le=50)
    displacement_atr: float = Field(default=1.5, ge=0.5, le=5)  # body >= this x ATR (setup timeframe)
    fvg_min_atr: float = Field(default=0.25, ge=0, le=3)
    sweep_min_pct: float = Field(default=0.02, ge=0, le=0.5)  # how far beyond the pool, % of price
    sweep_reclaim: int = Field(default=2, ge=1, le=5)  # candles allowed to close back inside
    sweep_lookback: int = Field(default=6, ge=2, le=24)  # setup candles between the sweep and the break
    equal_level_pct: float = Field(default=0.03, ge=0, le=0.5)  # equal highs/lows tolerance, % of price
    opening_range_minutes: int = Field(default=15, ge=5, le=60)
    poi: Literal["fvg", "ob", "either", "both"] = "either"  # where to enter: FVG, order block, either, overlap
    ob_zone: Literal["body", "range"] = "body"
    poi_max_age: int = Field(default=12, ge=2, le=48)  # setup candles the POI waits for a tap
    confirm_bars: int = Field(default=10, ge=2, le=60)  # entry candles from the tap to the confirming CHoCH
    premium_discount: bool = True  # longs only in discount, shorts only in premium (bias dealing range)
    min_room_r: float = Field(default=1.5, ge=0, le=10)  # opposing liquidity at least this many R away


class SmcOption(_Model):
    """Which option to buy for a signal: bullish buys a call, bearish a put."""

    strike: Strike = Field(default_factory=Strike)  # atm + offset (positive = OTM, negative = ITM) or premium
    expiry: Literal["nearest", "next"] = "nearest"
    expiry_day_cutoff: HHMM | None = "12:00"  # on expiry day, from this time use the next expiry
    min_volume: int = Field(default=10_000, ge=0)  # contracts traded today (quantity)
    min_oi: int = Field(default=50_000, ge=0)
    max_spread_pct: float = Field(default=1.0, gt=0, le=20)  # (ask - bid) / mid, %


class SmcRisk(_Model):
    lots: int = Field(default=1, ge=1, le=MAX_LOTS)
    rr: Literal[2, 3, 4] = 2  # TP3 in R; TP1 = 1R, TP2 halfway between
    tranches: bool = True  # split the lots over TP1 / TP2 / TP3 (with fewer than 3 lots: the later targets)
    sl_buffer_pct: float = Field(default=0.03, ge=0, le=1)  # beyond the sweep extreme, % of price
    min_risk_pct: float = Field(default=0.05, ge=0, le=2)  # a tighter stop is noise for an option: no trade
    max_risk_pct: float = Field(default=0.35, gt=0, le=3)  # a wider stop is too much risk: no trade
    premium_stop_pct: float | None = Field(default=40, gt=0, le=100)  # also exit when the premium falls this much
    breakeven_at_tp1: bool = True
    trail_at_tp2: bool = True  # at TP2 the stop moves to TP1
    max_trades_per_day: int = Field(default=2, ge=1, le=20)
    max_losses_per_day: int = Field(default=2, ge=1, le=20)
    cooldown_minutes: int = Field(default=15, ge=0, le=240)


class SmcSession(_Model):
    start: HHMM = "09:30"  # first entry (the opening range must be over)
    last_entry: HHMM = "14:30"
    exit: HHMM = "15:10"


class SmcScalpConfig(_Model):
    """Intraday options buying on Smart Money Concepts read from the index: a clear bias on the bias timeframe, a
    liquidity sweep, displacement and BOS/CHoCH on the setup timeframe leaving an order block or FVG, and a tap of
    it confirmed by a CHoCH on the entry timeframe. Stop beyond the sweep, TP1/TP2/TP3 in R (ADR 0018)."""

    kind: Literal["smc_scalp"] = "smc_scalp"
    underlying: Underlying = "NIFTY"
    timeframes: SmcTimeframes = Field(default_factory=SmcTimeframes)
    rules: SmcRules = Field(default_factory=SmcRules)
    option: SmcOption = Field(default_factory=SmcOption)
    risk: SmcRisk = Field(default_factory=SmcRisk)
    session: SmcSession = Field(default_factory=SmcSession)


StrategyConfig = Annotated[
    TimeBasedConfig | RangeBreakoutConfig | ZeroDteConfig | SmcScalpConfig, Field(discriminator="kind")
]
StrategyKind = Literal["time_based", "range_breakout", "zero_dte", "smc_scalp"]
AnyConfig = TimeBasedConfig | RangeBreakoutConfig | ZeroDteConfig | SmcScalpConfig
_ADAPTER: TypeAdapter[AnyConfig] = TypeAdapter(StrategyConfig)


# -- validation ------------------------------------------------------------------------------------------------
Loc = tuple[str | int, ...]


@dataclass(frozen=True)
class Issue:
    loc: Loc  # path inside the config, e.g. ("legs", 0, "stop_loss", "value")
    msg: str
    type: str = "value_error"


def parse(raw: Any) -> AnyConfig:
    """Raw JSON -> a config model. Raises pydantic.ValidationError."""
    return _ADAPTER.validate_python(raw)


def parse_issues(exc: ValidationError) -> list[Issue]:
    """Pydantic errors as Issues, without the union tag Pydantic puts first (("time_based", "legs", 0, ...))."""
    kinds = {"time_based", "range_breakout", "zero_dte", "smc_scalp"}
    out = []
    for e in exc.errors():
        loc = tuple(e["loc"])
        if loc and loc[0] in kinds:
            loc = loc[1:]
        out.append(Issue(loc, e["msg"], e["type"]))
    return out


def _hours(inst: Instrument, t: str, loc: Loc) -> list[Issue]:
    if inst.session_open <= t <= inst.session_close:
        return []
    return [Issue(loc, f"must be within {inst.code} trading hours ({inst.session_open}-{inst.session_close})")]


def _times(c: AnyConfig, inst: Instrument, order: list[str]) -> list[Issue]:
    issues = [i for k in order for i in _hours(inst, getattr(c, k), (k,))]
    for a, b in pairwise(order):
        if getattr(c, a) > getattr(c, b):
            issues.append(Issue((b,), f"must not be before {a.replace('_', ' ')} ({getattr(c, a)})"))
    return issues


def _threshold(t: Threshold, loc: Loc, action: str, is_target: bool) -> list[Issue]:
    if t.unit != "percent":
        return []
    if t.basis == "underlying" and t.value > 10:
        return [Issue((*loc, "value"), "an index move above 10% is not a useful stop or target")]
    # a premium cannot fall more than 100%: a buyer's stop-loss or a seller's target beyond that never triggers
    falls = (action == "BUY") != is_target
    if t.basis == "premium" and falls and t.value > 100:
        return [Issue((*loc, "value"), "the premium cannot fall more than 100%")]
    return []


def check(c: AnyConfig, instruments: Instruments) -> list[Issue]:
    """Rules that span fields, against the current instruments. An empty list means the config can be saved."""
    inst = instruments.get(c.underlying)
    if inst is None:
        return [Issue(("underlying",), f"{c.underlying} is not available for trading right now")]
    issues: list[Issue] = []
    if isinstance(c, TimeBasedConfig):
        for k in ("entry", "exit"):
            issues += _hours(inst, getattr(c.timing, k), ("timing", k))
        if c.timing.entry >= c.timing.exit:
            issues.append(Issue(("timing", "exit"), "must be after the entry time"))
        if len(set(c.timing.days)) != len(c.timing.days):
            issues.append(Issue(("timing", "days"), "lists a day twice"))
        seen: set[str] = set()
        for i, leg in enumerate(c.legs):
            loc: Loc = ("legs", i)
            if leg.id in seen:
                issues.append(Issue((*loc, "id"), f"leg id {leg.id} is used twice"))
            seen.add(leg.id)
            if leg.expiry in WEEKLY_EXPIRIES and not inst.weekly_expiry:
                issues.append(Issue((*loc, "expiry"), f"{c.underlying} has monthly expiries only"))
            if leg.strike.mode == "premium" and leg.strike.premium is None:
                issues.append(Issue((*loc, "strike", "premium"), "enter the premium to look for"))
            if leg.stop_loss:
                issues += _threshold(leg.stop_loss, (*loc, "stop_loss"), leg.action, is_target=False)
            if leg.target:
                issues += _threshold(leg.target, (*loc, "target"), leg.action, is_target=True)
            if leg.trailing and not leg.stop_loss:
                issues.append(Issue((*loc, "trailing"), "a trailing stop needs a stop-loss to trail"))
            if leg.trailing and leg.trailing.unit == "percent" and leg.trailing.step > 100:
                issues.append(Issue((*loc, "trailing", "step"), "must be at most 100%"))
            if leg.reentry_on_sl and not leg.stop_loss:
                issues.append(Issue((*loc, "reentry_on_sl"), "re-entry after a stop-loss needs a stop-loss"))
            if leg.reentry_on_target and not leg.target:
                issues.append(Issue((*loc, "reentry_on_target"), "re-entry after a target needs a target"))
        if c.risk.exit_all_on_leg_sl and not any(leg.stop_loss for leg in c.legs):
            issues.append(Issue(("risk", "exit_all_on_leg_sl"), "no leg has a stop-loss"))
        return issues
    if isinstance(c, SmcScalpConfig):
        return issues + _check_smc(c, inst)

    if not inst.weekly_expiry:
        issues.append(Issue(("underlying",), f"this strategy trades weekly expiries; {c.underlying} has none"))
    if c.itm_points % inst.strike_step:
        issues.append(
            Issue(("itm_points",), f"must be a multiple of {c.underlying}'s strike step ({inst.strike_step})")
        )
    if c.hedge_width is not None and c.hedge_width % inst.strike_step:
        issues.append(Issue(("hedge_width",), f"must be a multiple of {inst.strike_step}"))
    if isinstance(c, RangeBreakoutConfig):
        issues += _times(c, inst, ["range_start", "range_end", "last_entry", "exit_time"])
        if c.range_start == c.range_end:
            issues.append(Issue(("range_end",), "the range needs at least one minute"))
    else:
        issues += _times(c, inst, ["first_entry", "last_entry", "exit_time"])
        if c.last_entry == c.exit_time:
            issues.append(Issue(("exit_time",), "must be after the last entry time"))
    return issues


def _check_smc(c: SmcScalpConfig, inst: Instrument) -> list[Issue]:
    issues: list[Issue] = []
    ss = c.session
    for k in ("start", "last_entry", "exit"):
        issues += _hours(inst, getattr(ss, k), ("session", k))
    if ss.start > ss.last_entry:
        issues.append(Issue(("session", "last_entry"), f"must not be before the first entry ({ss.start})"))
    if ss.last_entry >= ss.exit:
        issues.append(Issue(("session", "exit"), "must be after the last entry time"))
    tf = c.timeframes
    if not tf.entry < tf.setup < tf.bias:
        issues.append(Issue(("timeframes", "setup"), "timeframes must grow: entry < setup < bias"))
    elif tf.bias % tf.setup or tf.setup % tf.entry:
        issues.append(Issue(("timeframes", "bias"), "each timeframe must be a multiple of the one below it"))
    r = c.risk
    if r.min_risk_pct >= r.max_risk_pct:
        issues.append(Issue(("risk", "max_risk_pct"), "must be above the minimum risk"))
    if c.option.strike.mode == "premium" and c.option.strike.premium is None:
        issues.append(Issue(("option", "strike", "premium"), "enter the premium to look for"))
    if c.option.expiry == "next" and c.option.expiry_day_cutoff is not None:
        issues.append(Issue(("option", "expiry_day_cutoff"), "only applies to the nearest expiry; clear it"))
    return issues


def plan_warnings(c: AnyConfig, max_lots_per_order: int | None) -> list[Issue]:
    """What the user's plan will not let this config do. Saving is allowed; deploying is not (phase 9)."""
    if max_lots_per_order is None:
        return []
    msg = f"your plan allows {max_lots_per_order} lot(s) per order"
    if isinstance(c, TimeBasedConfig):
        return [
            Issue(("legs", i, "lots"), msg, "plan_limit")
            for i, leg in enumerate(c.legs)
            if leg.lots > max_lots_per_order
        ]
    if isinstance(c, SmcScalpConfig):
        # tranches are separate orders, but the plan limit is about one order's size: the largest tranche
        return [Issue(("risk", "lots"), msg, "plan_limit")] if max_order_lots(c) > max_lots_per_order else []
    return [Issue(("lots",), msg, "plan_limit")] if c.lots > max_lots_per_order else []


def max_order_lots(c: AnyConfig) -> int:
    """The most lots this config puts in one order."""
    if isinstance(c, TimeBasedConfig):
        return max(leg.lots for leg in c.legs)
    if isinstance(c, SmcScalpConfig):
        return max(smc_tranches(c.risk.lots, c.risk.tranches))
    return c.lots


def smc_tranches(lots: int, split: bool) -> list[int]:
    """Lots for TP1, TP2, TP3 (0 = no tranche). Unsplit, or with fewer than 3 lots, the later targets get them."""
    if not split:
        return [0, 0, lots]
    if lots < 3:
        return [0, 1, 1] if lots == 2 else [0, 0, 1]
    base, extra = divmod(lots, 3)
    return [base, base + (1 if extra == 2 else 0), base + (1 if extra >= 1 else 0)]


def migrate(schema_version: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a stored config to SCHEMA_VERSION (there is only version 1 so far)."""
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unknown strategy schema version {schema_version}")
    return raw


SMC_PRESET_UNDERLYINGS: tuple[Underlying, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")


# -- presets ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    config: AnyConfig


def _leg(id_: str, action: Literal["BUY", "SELL"], opt: Literal["CE", "PE"], offset: int = 0, **kw: Any) -> Leg:
    return Leg(id=id_, action=action, option_type=opt, strike=Strike(offset=offset), **kw)


_SL30 = Threshold(unit="percent", value=30)

PRESETS: tuple[Preset, ...] = (
    Preset(
        "blank",
        "Start from scratch",
        "One leg to build on: buy the ATM call at 09:20, exit at 15:15.",
        TimeBasedConfig(legs=[_leg("L1", "BUY", "CE")]),
    ),
    Preset(
        "short_straddle",
        "Short straddle",
        "Sell the ATM call and put at 09:20 with a 30% stop-loss on each, exit at 15:15.",
        TimeBasedConfig(legs=[_leg("L1", "SELL", "CE", stop_loss=_SL30), _leg("L2", "SELL", "PE", stop_loss=_SL30)]),
    ),
    Preset(
        "short_strangle",
        "Short strangle",
        "Sell a call and a put two strikes out of the money with a 40% stop-loss on each.",
        TimeBasedConfig(
            legs=[
                _leg("L1", "SELL", "CE", 2, stop_loss=Threshold(value=40)),
                _leg("L2", "SELL", "PE", 2, stop_loss=Threshold(value=40)),
            ]
        ),
    ),
    Preset(
        "iron_condor",
        "Iron condor",
        "Sell two strikes out of the money and buy protection six strikes out, on both sides.",
        TimeBasedConfig(
            legs=[
                _leg("L1", "SELL", "CE", 2),
                _leg("L2", "SELL", "PE", 2),
                _leg("L3", "BUY", "CE", 6),
                _leg("L4", "BUY", "PE", 6),
            ],
            risk=StrategyRisk(mtm_stop_loss=3000),
        ),
    ),
    Preset(
        "range_breakout",
        "2h range breakout",
        "Proven positional seller: trade the first breakout of the 09:15-11:15 range, hedged, held to expiry.",
        RangeBreakoutConfig(),
    ),
    Preset(
        "zero_dte",
        "Expiry-day ITM straddle",
        "Proven 0DTE seller: sell ITM call and put on expiry day at the best recent entry time.",
        ZeroDteConfig(),
    ),
    *(
        Preset(
            f"smc_scalp_{u.lower()}",
            f"SMC options scalper ({u})",
            "Buys a call or put on a liquidity sweep, displacement and BOS/CHoCH from an order block or FVG, "
            "with the 15m trend; stop beyond the sweep, targets at 1R / 1.5R / 2R.",
            SmcScalpConfig(underlying=u),
        )
        for u in SMC_PRESET_UNDERLYINGS
    ),
)


def default_config() -> TimeBasedConfig:
    return TimeBasedConfig(legs=[_leg("L1", "BUY", "CE")])
