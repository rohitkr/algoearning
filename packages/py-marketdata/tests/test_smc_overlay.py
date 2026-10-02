"""The charts' SMC overlay: the library's output turned into shapes, and how a new candle changes it."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from ae_marketdata.candles import Candle
from ae_marketdata.smc_overlay import compute, frame, swings
from ae_marketdata.types import IST

T0 = datetime(2026, 10, 5, 9, 15, tzinfo=IST)


def walk(n: int, seed: int = 7, start: float = 25000.0) -> list[Candle]:
    rng = random.Random(seed)
    out, p = [], start
    for i in range(n):
        day, slot = divmod(i, 75)
        o = p
        hi = lo = p
        for _ in range(5):
            p *= 1 + rng.gauss(0, 0.0012)
            hi, lo = max(hi, p), min(lo, p)
        out.append(Candle(T0 + timedelta(days=day, minutes=5 * slot), o, hi, lo, p))
    return out


def test_frame_is_what_the_library_expects() -> None:
    df = frame(walk(3))
    assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert list(df.index) == [0, 1, 2]


def test_shapes_are_well_formed_and_point_at_real_candles() -> None:
    candles = walk(300)
    times = {int(c.ts.timestamp()) for c in candles}
    ov = compute(candles, 5)
    assert ov.candles == 300
    kinds = {b.kind for b in ov.boxes} | {line.kind for line in ov.lines}
    assert {"fvg", "ob", "bos"} <= kinds
    for b in ov.boxes:
        assert b.top >= b.bottom and b.start in times
        assert b.end is None or (b.end in times and b.end >= b.start)
    for line in ov.lines:
        assert line.start in times and (line.end is None or (line.end in times and line.end > line.start))
    # two sessions in the data: yesterday's high and low are levels
    assert {lv.kind for lv in ov.levels} >= {"pdh", "pdl"}
    pdh = next(lv.price for lv in ov.levels if lv.kind == "pdh")
    assert abs(pdh - max(c.high for c in candles[150:225])) < 0.1


def test_no_swing_is_invented_on_the_first_or_newest_candle() -> None:
    candles = walk(120)
    shl = swings(frame(candles), 5)
    assert shl["HighLow"].iloc[0] != shl["HighLow"].iloc[0]  # NaN
    assert shl["HighLow"].iloc[-1] != shl["HighLow"].iloc[-1]
    newest = int(candles[-1].ts.timestamp())
    assert all(line.start != newest for line in compute(candles, 5).lines)


def test_a_bullish_gap_is_open_until_a_later_candle_fills_it() -> None:
    base = walk(40, seed=3, start=100.0)
    last = base[-1]
    t = last.ts

    def c(i: int, o: float, h: float, low: float, cl: float) -> Candle:
        return Candle(t + timedelta(minutes=5 * i), o, h, low, cl)

    p = last.close
    gap = [c(1, p, p + 1, p - 1, p + 0.5), c(2, p + 0.5, p + 6, p + 0.4, p + 5.8), c(3, p + 5.8, p + 7, p + 3, p + 6.5)]
    ov = compute([*base, *gap], 5)
    fvg = [b for b in ov.boxes if b.kind == "fvg" and b.side == "bull" and b.start == int(gap[0].ts.timestamp())]
    assert len(fvg) == 1 and fvg[0].end is None
    assert (fvg[0].bottom, fvg[0].top) == (p + 1, p + 3)
    # a new candle trades back into the gap: the same box now ends there
    fill = c(4, p + 6.5, p + 6.6, p + 2, p + 2.5)
    ov2 = compute([*base, *gap, fill], 5)
    filled = [b for b in ov2.boxes if b.kind == "fvg" and b.start == fvg[0].start]
    assert len(filled) == 1 and filled[0].end == int(fill.ts.timestamp())


def test_too_few_candles_give_an_empty_overlay() -> None:
    ov = compute(walk(8), 5)
    assert (ov.boxes, ov.lines, ov.levels) == ([], [], [])


def test_order_block_strength_only_with_volume() -> None:
    plain = compute(walk(300), 5)
    assert all(b.label == "OB" for b in plain.boxes if b.kind == "ob")
    vol = [Candle(c.ts, c.open, c.high, c.low, c.close, 1000 + i) for i, c in enumerate(walk(300))]
    assert any(b.label.endswith("%") for b in compute(vol, 5).boxes if b.kind == "ob")
