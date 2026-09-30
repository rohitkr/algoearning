"""Supported brokers. Adding a broker = one adapter module + one entry here."""

from __future__ import annotations

from .base import BrokerAdapter, BrokerInfo
from .zerodha import ZerodhaAdapter

COMING_SOON = (
    BrokerInfo("upstox", "Upstox", False, "https://account.upstox.com/developer/apps"),
    BrokerInfo("angelone", "Angel One", False, "https://smartapi.angelbroking.com/"),
)


def default_adapters() -> dict[str, BrokerAdapter]:
    return {"zerodha": ZerodhaAdapter()}


def catalog(adapters: dict[str, BrokerAdapter]) -> list[BrokerInfo]:
    return [a.info for a in adapters.values()] + [b for b in COMING_SOON if b.code not in adapters]
