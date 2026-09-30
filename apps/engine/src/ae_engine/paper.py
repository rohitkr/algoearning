"""The paper exchange: fills an order at once at the feed's last price, a little worse (slippage), rounded to the
exchange tick. No price means no fill (the runner is told and tries again later)."""

from __future__ import annotations

from dataclasses import dataclass

from ae_core.trading.model import Intent, Market

TICK = 0.05


def to_tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


@dataclass
class PaperExchange:
    slippage_pct: float = 0.05  # of the price, against the order (0.05%: a few paise on a 100-rupee option)

    def fill(self, intent: Intent, m: Market) -> float | None:
        ltp = m.price(intent.contract)
        if ltp is None or ltp <= 0:
            return None
        slip = max(TICK, ltp * self.slippage_pct / 100)
        return max(TICK, to_tick(ltp + slip if intent.side == "BUY" else ltp - slip))
