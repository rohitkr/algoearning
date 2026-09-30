"""What a saved strategy means. The API validates every save with this module and the trading engine (phase 9)
reads configs with it, so the two can never disagree about a field (ADR 0010).

A config is one of three kinds, told apart by `kind`:

    time_based      the builder: 1-6 option legs entered at a fixed time on chosen weekdays, each with its own
                    strike rule, stop-loss, target, trailing stop and re-entry, plus strategy-wide MTM limits
    range_breakout  the proven positional 2h range breakout seller (algo-trading-claude, RangeBreakoutParams)
    zero_dte        the proven expiry-day ITM straddle seller (algo-trading-claude, ZeroDteParams)

Validation has two layers: the Pydantic models check types and bounds; `check()` checks rules that span
fields (entry before exit, weekly expiry only where the exchange lists one, trailing needs a stop-loss, ...).
`plan_warnings()` compares a config with the user's plan without refusing it: the engine enforces plan limits
when the strategy is deployed.

Configs belong to the user who saved them. Only the instrument catalogue below is platform-wide, because lot
sizes, strike steps and expiry days are set by the exchange, not by users.

Changing the shape of a config: bump SCHEMA_VERSION and teach `migrate()` to upgrade older configs."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

SCHEMA_VERSION = 1
MARKET_OPEN, MARKET_CLOSE = "09:15", "15:30"
MAX_LEGS = 6
MAX_STRIKE_OFFSET = 20  # strikes away from ATM
MAX_LOTS = 100  # sanity cap per leg; plans usually allow far fewer (max_lots_per_order)


# -- instruments (platform-wide exchange facts) ---------------------------------------------------------------
@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    exchange: str  # derivatives segment: NFO (NSE) or BFO (BSE)
    lot_size: int
    strike_step: int
    weekly_expiry: bool  # False: only monthly expiries are listed


INSTRUMENTS: dict[str, Instrument] = {
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


StrategyConfig = Annotated[TimeBasedConfig | RangeBreakoutConfig | ZeroDteConfig, Field(discriminator="kind")]
StrategyKind = Literal["time_based", "range_breakout", "zero_dte"]
_ADAPTER: TypeAdapter[TimeBasedConfig | RangeBreakoutConfig | ZeroDteConfig] = TypeAdapter(StrategyConfig)
AnyConfig = TimeBasedConfig | RangeBreakoutConfig | ZeroDteConfig


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
    kinds = {"time_based", "range_breakout", "zero_dte"}
    out = []
    for e in exc.errors():
        loc = tuple(e["loc"])
        if loc and loc[0] in kinds:
            loc = loc[1:]
        out.append(Issue(loc, e["msg"], e["type"]))
    return out


def _in_market(t: str) -> bool:
    return MARKET_OPEN <= t <= MARKET_CLOSE


def _times(c: AnyConfig, order: list[str]) -> list[Issue]:
    issues = [
        Issue((k,), f"must be within market hours ({MARKET_OPEN}-{MARKET_CLOSE})")
        for k in order
        if not _in_market(getattr(c, k))
    ]
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


def check(c: AnyConfig) -> list[Issue]:
    """Rules that span fields. An empty list means the config can be saved."""
    inst = INSTRUMENTS[c.underlying]
    issues: list[Issue] = []
    if isinstance(c, TimeBasedConfig):
        for k in ("entry", "exit"):
            if not _in_market(getattr(c.timing, k)):
                issues.append(Issue(("timing", k), f"must be within market hours ({MARKET_OPEN}-{MARKET_CLOSE})"))
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

    if not inst.weekly_expiry:
        issues.append(Issue(("underlying",), f"this strategy trades weekly expiries; {c.underlying} has none"))
    if c.itm_points % inst.strike_step:
        issues.append(
            Issue(("itm_points",), f"must be a multiple of {c.underlying}'s strike step ({inst.strike_step})")
        )
    if c.hedge_width is not None and c.hedge_width % inst.strike_step:
        issues.append(Issue(("hedge_width",), f"must be a multiple of {inst.strike_step}"))
    if isinstance(c, RangeBreakoutConfig):
        issues += _times(c, ["range_start", "range_end", "last_entry", "exit_time"])
        if c.range_start == c.range_end:
            issues.append(Issue(("range_end",), "the range needs at least one minute"))
    else:
        issues += _times(c, ["first_entry", "last_entry", "exit_time"])
        if c.last_entry == c.exit_time:
            issues.append(Issue(("exit_time",), "must be after the last entry time"))
    return issues


def max_leg_lots(c: AnyConfig) -> int:
    return max(leg.lots for leg in c.legs) if isinstance(c, TimeBasedConfig) else c.lots


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
    return [Issue(("lots",), msg, "plan_limit")] if c.lots > max_lots_per_order else []


def migrate(schema_version: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a stored config to SCHEMA_VERSION (there is only version 1 so far)."""
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unknown strategy schema version {schema_version}")
    return raw


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
)


def default_config() -> TimeBasedConfig:
    return TimeBasedConfig(legs=[_leg("L1", "BUY", "CE")])
