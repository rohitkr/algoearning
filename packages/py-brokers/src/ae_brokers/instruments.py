"""Exchange facts for the platform's instruments, from Zerodha's public daily instrument list
(https://api.kite.trade/instruments/<EXCHANGE>, CSV, no login needed; the local app's zerodha/instruments.py).

For an underlying, the nearest listed option expiry gives the lot size (the one being traded now: when SEBI changes
a lot size, new contracts carry it), the finest strike spacing, and whether expiries are weekly (the first two are
at most 8 days apart) or monthly."""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import httpx

DUMP_URL = "https://api.kite.trade/instruments/{exchange}"


@dataclass(frozen=True)
class InstrumentFacts:
    code: str
    lot_size: int
    strike_step: int
    weekly_expiry: bool
    nearest_expiry: date
    expiries: tuple[date, ...] = ()  # every listed option expiry from today, sorted


def derive_facts(rows: Iterable[Mapping[str, str]], code: str, today: date) -> InstrumentFacts | None:
    """None when the list has no live options for `code` (the caller then keeps what it had)."""
    by_expiry: dict[date, list[Mapping[str, str]]] = {}
    for r in rows:
        if r.get("name") != code or r.get("instrument_type") not in ("CE", "PE") or not r.get("expiry"):
            continue
        exp = date.fromisoformat(r["expiry"])
        if exp >= today:
            by_expiry.setdefault(exp, []).append(r)
    if not by_expiry:
        return None
    expiries = sorted(by_expiry)
    nearest = by_expiry[expiries[0]]
    lot = Counter(int(float(r["lot_size"])) for r in nearest).most_common(1)[0][0]
    strikes = sorted({float(r["strike"]) for r in nearest if r["instrument_type"] == "CE"})
    gaps = [b - a for a, b in pairwise(strikes) if b > a]
    if lot <= 0 or not gaps:
        return None
    weekly = len(expiries) > 1 and (expiries[1] - expiries[0]).days <= 8
    return InstrumentFacts(code, lot, int(min(gaps)), weekly, expiries[0], tuple(expiries))


def parse_dump(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


async def fetch_dump(exchange: str, client: httpx.AsyncClient) -> list[dict[str, str]]:
    r = await client.get(DUMP_URL.format(exchange=exchange), timeout=60)
    r.raise_for_status()
    return parse_dump(r.text)
