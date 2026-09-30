"""Prepaid plan periods. Pure maths, no I/O.

A payment buys one period of a plan (month = 30 days, year = 365 days: predictable, no calendar edge cases).
    no paid plan running        -> starts now
    same plan still running     -> extends from its current end (paying early never loses days)
    a different plan running    -> starts now, and the unused value of the old plan is credited as extra time on
                                   the new one at the new plan's price: switching never charges twice
                                   (upgrade: fewer extra days, downgrade: more)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

PERIOD_DAYS = {"month": 30, "year": 365}


@dataclass(frozen=True)
class RunningPlan:
    plan_code: str
    price_paise: int
    interval: str
    period_end: datetime


@dataclass(frozen=True)
class NewPeriod:
    start: datetime
    end: datetime
    extends_current: bool  # same plan: the existing subscription is extended
    credit_paise: int  # unused value carried over from a different plan
    credit_seconds: int


def period(interval: str) -> timedelta:
    try:
        return timedelta(days=PERIOD_DAYS[interval])
    except KeyError:
        raise ValueError(f"unknown billing interval {interval!r}") from None


def next_period(
    now: datetime, plan_code: str, price_paise: int, interval: str, running: RunningPlan | None
) -> NewPeriod:
    length = period(interval)
    if running is None or running.period_end <= now:
        return NewPeriod(now, now + length, False, 0, 0)
    if running.plan_code == plan_code:
        return NewPeriod(now, running.period_end + length, True, 0, 0)
    old_len = period(running.interval).total_seconds()
    remaining = (running.period_end - now).total_seconds()
    credit = int(running.price_paise * remaining / old_len)  # value of the unused time, rounded down
    credit_s = int(credit / price_paise * length.total_seconds()) if price_paise > 0 else 0
    return NewPeriod(now, now + length + timedelta(seconds=credit_s), False, credit, credit_s)
