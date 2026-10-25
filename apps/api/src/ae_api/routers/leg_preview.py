"""The legs a strategy would trade right now (the strategy and run pages show them with a payoff chart): the engine's
own strike choice (RulesRunner.contracts_for) on the feed's live prices. Nothing is traded or stored."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from ae_core.strategy import PREMIUM_MODES, PlansConfig, RulesConfig, StrategyConfig, parse
from ae_core.trading import options
from ae_core.trading.model import IST, Contract, Market
from ae_core.trading.runners import RulesRunner
from ae_db.models import Instrument
from ae_marketdata.backfill import calendar_holidays
from ae_marketdata.hub import Hub
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..deps import CurrentUser, UserSession
from ..errors import Unavailable
from ..schemas import ERROR_RESPONSES

router = APIRouter(tags=["strategies"], responses=ERROR_RESPONSES)
WAIT_S = 2.5  # contracts the feed was not streaming yet are asked for: their first prices come within a second or two


class LegPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: StrategyConfig
    multiplier: int = Field(default=1, ge=1, le=100)


class PreviewLeg(BaseModel):
    leg: str
    action: str  # BUY | SELL
    option_type: str  # CE | PE
    strike: int
    expiry: date
    lots: int
    qty: int
    price: float | None
    tradingsymbol_hint: str  # e.g. NIFTY 27 Oct 22600 CE


class PreviewSet(BaseModel):
    plan: str  # the plan's name ("" for a single-plan rules strategy)
    direction: str  # always | up | down (legs chosen by a signal's direction)
    underlying: str
    spot: float | None
    lot_size: int
    legs: list[PreviewLeg]
    reason: str | None = None  # why the legs cannot be chosen right now
    prices_at: datetime | None = None


def _plans(config: Any) -> list[tuple[str, RulesConfig]]:
    if isinstance(config, PlansConfig):
        return [(p.name, r) for p, r in config.rules()]
    if isinstance(config, RulesConfig):
        return [("", config)]
    return []


@router.post("/v1/strategies/preview-legs", response_model=list[PreviewSet])
async def preview_legs(body: LegPreviewIn, user: CurrentUser, s: UserSession, request: Request) -> list[PreviewSet]:
    """For every plan: the contracts its legs would trade now (ATM from the live index, premium-based legs from the
    live option prices, a premium hedge always beyond the strike it hedges) and their prices. Signal strategies get
    one set per direction; a Telegram tip traded as given has no legs of its own, so it gets none."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise Unavailable("live prices are not available")
    hub = Hub(redis)
    config = parse(body.config.model_dump(mode="json"))
    insts = {i.code: i for i in (await s.execute(select(Instrument))).scalars()}
    now = datetime.now(IST)
    holidays = frozenset(calendar_holidays())
    out: list[PreviewSet] = []
    for name, rules in _plans(config):
        e = rules.entry
        if e.mode == "tip" and e.tip_trade == "as_given":
            continue
        inst = insts.get(rules.underlying)
        if inst is None:
            continue
        u = rules.underlying
        spot_tick = (await hub.last([u])).get(u)
        expiries = sorted(date.fromisoformat(x) for x in inst.expiries)
        m = Market(now, u, spot_tick.ltp if spot_tick else None, [], {}, expiries, inst.lot_size, inst.strike_step,
                   holidays=holidays)  # fmt: skip
        runner = RulesRunner(rules, body.multiplier)
        legs = rules.legs_for(now.date()) or next((ls for ls in (rules.day_legs or {}).values() if ls), rules.legs)
        legs = [leg for leg in legs if leg.enabled]
        dirs = sorted({leg.direction for leg in legs} - {"always"}) or ["always"]
        # the contracts any leg might pick: ask the feed for them and read their prices
        keys: set[str] = set()
        for leg in legs:
            expiry = runner._leg_expiry(m, leg)
            if expiry is None or m.spot is None:
                continue
            if leg.strike.mode in PREMIUM_MODES:
                keys |= {c.key for c in options.candidates(m, leg.option_type, expiry)}
            elif isinstance(r := options.pick(m, leg.option_type, leg.strike, expiry), Contract):
                keys.add(r.key)
        ticks = await hub.last(keys)
        if keys and len(ticks) < len(keys):
            await hub.want(keys)
            for _ in range(int(WAIT_S / 0.25)):
                await asyncio.sleep(0.25)
                ticks = await hub.last(keys)
                if len(ticks) == len(keys):
                    break
        m.prices = {k: t.ltp for k, t in ticks.items()}
        stamps = [t.ts for t in ticks.values()] + ([spot_tick.ts] if spot_tick else [])
        for d in dirs:
            chosen = [leg for leg in legs if leg.direction in ("always", d)]
            got = runner.contracts_for(m, chosen) if m.spot is not None else "no index price yet"
            rows = []
            if not isinstance(got, str):
                for leg, c in got:
                    lots = leg.lots * body.multiplier
                    rows.append(PreviewLeg(leg=leg.id, action=leg.action, option_type=c.right, strike=c.strike,
                                           expiry=c.expiry, lots=lots, qty=lots * inst.lot_size, price=m.price(c),
                                           tradingsymbol_hint=c.label))  # fmt: skip
            out.append(PreviewSet(plan=name, direction=d, underlying=u, spot=m.spot, lot_size=inst.lot_size,
                                  legs=rows, reason=got if isinstance(got, str) else None,
                                  prices_at=min(stamps) if stamps else None))  # fmt: skip
    return out
