"""What users can be told about, and how it reads. Pure: the engine queues notifications with `compose`, the worker
sends them, the API lists the catalogue for the settings page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    hint: str
    default: bool  # on for a user who never touched the settings
    urgent: bool = False  # sent even when the user muted everything else? no: only marks the tone


EVENTS: dict[str, EventType] = {
    e.key: e
    for e in (
        EventType("trade_opened", "A position was opened", "Every entry a strategy makes", False),
        EventType("trade_closed", "A position was closed", "Every exit, with its profit or loss", False),
        EventType("stop_loss", "A stop-loss or target hit", "When a leg exits on its stop-loss or target", True),
        EventType(
            "order_problem", "An order failed", "Entry refused, exit failing and retrying, margin short", True, True
        ),
        EventType(
            "risk_limit", "A risk limit was reached", "Daily loss or profit, kill switch, platform halt", True, True
        ),
        EventType("broker_login", "Broker login needed", "Your Zerodha session expired", True, True),
        EventType("run_stopped", "A strategy stopped or failed", "Stopped by you, by a limit, or by an error", True),
        EventType(
            "position_mismatch", "Positions differ from your broker", "Reconciliation found a difference", True, True
        ),
        EventType(
            "engine_down", "The engine stopped responding", "Running strategies are not being watched", True, True
        ),
    )
}

CHANNELS = ("email", "telegram")

# engine event name -> (notification type, title)
FROM_ENGINE: dict[str, tuple[str, str]] = {
    "order_failed": ("order_problem", "Order failed"),
    "exit_failed": ("order_problem", "Exit failing: retrying"),
    "order_not_placed": ("order_problem", "Order not placed"),
    "risk_limit": ("risk_limit", "Risk limit reached"),
    "run_error": ("run_stopped", "Strategy stopped by an error"),
    "run_stopped": ("run_stopped", "Strategy stopped"),
    "position_closed_outside": ("position_mismatch", "Position closed outside AlgoEarning"),
    "position_mismatch": ("position_mismatch", "Positions differ from your broker"),
}


def enabled_events(chosen: list[str] | None) -> set[str]:
    """The events a user gets: their choice, or the defaults when they never chose."""
    if chosen is None:
        return {k for k, e in EVENTS.items() if e.default}
    return {k for k in chosen if k in EVENTS}


def compose(strategy: str | None, title: str, detail: dict[str, Any]) -> str:
    """One short plain-text body from an engine event's detail."""
    parts = []
    if strategy:
        parts.append(f"Strategy: {strategy}")
    for k in (
        "contract",
        "side",
        "qty",
        "price",
        "pnl",
        "reason",
        "error",
        "symbol",
        "expected",
        "at_zerodha",
        "message",
    ):
        if detail.get(k) not in (None, ""):
            parts.append(f"{k.replace('_', ' ').capitalize()}: {detail[k]}")
    return "\n".join(parts) or title
