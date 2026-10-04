"""Indicators over candles, as charting platforms compute them (ADR 0022). Every function returns one value per
candle, aligned with its input, and None while there is not enough history yet. Smoothed averages (RSI, ATR, ADX,
Supertrend) use Wilder's method, seeded with a simple mean, like TradingView and Kite's charts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

Series = list[float | None]


class Candle(Protocol):
    @property
    def high(self) -> float: ...
    @property
    def low(self) -> float: ...
    @property
    def close(self) -> float: ...


def sma(values: Sequence[float], n: int) -> Series:
    out: Series = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: Sequence[float], n: int) -> Series:
    """Seeded with the simple mean of the first n values."""
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rma(values: Sequence[float], n: int, offset: int = 0) -> Series:
    """Wilder's smoothing of values[offset:], aligned with `values`."""
    out: Series = [None] * len(values)
    if len(values) - offset < n:
        return out
    prev = sum(values[offset : offset + n]) / n
    out[offset + n - 1] = prev
    for i in range(offset + n, len(values)):
        prev = (prev * (n - 1) + values[i]) / n
        out[i] = prev
    return out


def rsi(closes: Sequence[float], n: int = 14) -> Series:
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = _rma(gains, n, 1), _rma(losses, n, 1)
    for i in range(len(closes)):
        g, lo = ag[i], al[i]
        if g is not None and lo is not None:
            out[i] = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
    return out


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """(MACD line, signal line, histogram)."""
    f, s = ema(closes, fast), ema(closes, slow)
    line: Series = [a - b if a is not None and b is not None else None for a, b in zip(f, s, strict=True)]
    first = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig: Series = [None] * first
    sig += ema([v for v in line[first:] if v is not None], signal)
    hist: Series = [a - b if a is not None and b is not None else None for a, b in zip(line, sig, strict=True)]
    return line, sig, hist


def true_range(c: Sequence[Candle]) -> list[float]:
    return [
        c[i].high - c[i].low
        if i == 0
        else max(c[i].high - c[i].low, abs(c[i].high - c[i - 1].close), abs(c[i].low - c[i - 1].close))
        for i in range(len(c))
    ]


def atr(c: Sequence[Candle], n: int = 14) -> Series:
    return _rma(true_range(c), n)


def bollinger(closes: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[Series, Series, Series]:
    """(upper, middle, lower): the n-candle mean +/- k population standard deviations."""
    mid = sma(closes, n)
    up: Series = []
    lo: Series = []
    for i, m in enumerate(mid):
        if m is None:
            up.append(None)
            lo.append(None)
            continue
        window = closes[i - n + 1 : i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        up.append(m + k * sd)
        lo.append(m - k * sd)
    return up, mid, lo


def supertrend(c: Sequence[Candle], n: int = 10, k: float = 3.0) -> Series:
    """The Supertrend line: below the price in an uptrend, above it in a downtrend."""
    a = atr(c, n)
    out: Series = [None] * len(c)
    upper = lower = None
    trend_up = True
    for i, cd in enumerate(c):
        v = a[i]
        if v is None:
            continue
        mid = (cd.high + cd.low) / 2
        bu, bl = mid + k * v, mid - k * v
        prev_close = c[i - 1].close if i else cd.close
        if upper is None or lower is None:
            upper, lower, trend_up = bu, bl, cd.close >= mid
        else:
            upper = bu if bu < upper or prev_close > upper else upper
            lower = bl if bl > lower or prev_close < lower else lower
            if trend_up and cd.close < lower:
                trend_up = False
            elif not trend_up and cd.close > upper:
                trend_up = True
        out[i] = lower if trend_up else upper
    return out


def adx(c: Sequence[Candle], n: int = 14) -> tuple[Series, Series, Series]:
    """(ADX, +DI, -DI)."""
    size = len(c)
    plus = [0.0] * size
    minus = [0.0] * size
    for i in range(1, size):
        up, down = c[i].high - c[i - 1].high, c[i - 1].low - c[i].low
        plus[i] = up if up > down and up > 0 else 0.0
        minus[i] = down if down > up and down > 0 else 0.0
    tr = _rma(true_range(c), n, 1)
    sp, sm = _rma(plus, n, 1), _rma(minus, n, 1)
    pdi: Series = [None] * size
    mdi: Series = [None] * size
    dx: list[float] = []
    dx_at: list[int] = []
    for i in range(size):
        t, p, m = tr[i], sp[i], sm[i]
        if t is None or p is None or m is None or t == 0:
            continue
        pdi[i], mdi[i] = 100 * p / t, 100 * m / t
        s = pdi[i] + mdi[i]  # type: ignore[operator]
        dx.append(0.0 if s == 0 else 100 * abs(pdi[i] - mdi[i]) / s)  # type: ignore[operator]
        dx_at.append(i)
    out: Series = [None] * size
    smoothed = _rma(dx, n)
    for j, v in enumerate(smoothed):
        if v is not None:
            out[dx_at[j]] = v
    return out, pdi, mdi
