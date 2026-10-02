"""Smart Money Concepts zones for the charts, computed with the `smartmoneyconcepts` library (joshyattridge) over
closed candles and turned into plain shapes a chart can draw: boxes (FVGs, order blocks), dashed lines (BOS, CHoCH,
equal-high/low liquidity) and price levels (previous day high/low, nearest swing support/resistance).

The whole overlay is recomputed from all closed candles whenever a candle closes, and replaces the previous one. That
is what keeps the chart honest when a new bar changes history: an FVG gets mitigated, an order block turns into a
breaker or disappears, a structure break that was pending is confirmed, a swing is confirmed only once `swing_length`
later candles exist. Nothing is patched incrementally, so the drawing can never drift from the library's result.

Two corrections to the library's output, both about the live edge:
- `swing_highs_lows` forces a swing on the first and last candle so highs and lows alternate. A real swing needs
  `swing_length` candles on each side, so these two are artefacts; on a live chart the last one would be a fake
  swing on the newest candle and produce false BOS/CHoCH. They are removed before anything else uses the swings.
- Index candles carry no traded volume, so order-block strength (from volume) is reported only when there is volume.

The strategy engine has its own no-lookahead detectors (ae_core.trading.smc, ADR 0018); this overlay is for reading
the chart and may differ from them."""

from __future__ import annotations

import contextlib
import io
import math
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from .candles import Candle
from .types import IST

os.environ.setdefault("SMC_CREDIT", "0")  # the library prints a banner on import otherwise
with contextlib.redirect_stdout(io.StringIO()):
    from smartmoneyconcepts import smc

Side = Literal["bull", "bear"]

# how many of each are drawn, newest first (older ones are still in the calculation, just not on the chart)
MAX_FVG_OPEN = 12  # unmitigated gaps
MAX_FVG_FILLED = 8  # mitigated gaps, drawn faint
MAX_OB = 12
MAX_STRUCTURE = 20
MAX_LIQUIDITY = 12


@dataclass(frozen=True)
class Box:
    kind: Literal["fvg", "ob"]
    side: Side
    top: float
    bottom: float
    start: int  # epoch seconds of the first candle
    end: int | None  # candle that mitigated it; None = still open (extends to the live edge)
    label: str


@dataclass(frozen=True)
class Line:
    kind: Literal["bos", "choch", "liquidity"]
    side: Side
    price: float
    start: int
    end: int | None  # the breaking / sweeping candle; None = not taken yet
    label: str


@dataclass(frozen=True)
class Level:
    kind: Literal["pdh", "pdl", "resistance", "support"]
    price: float
    label: str


@dataclass
class Overlay:
    boxes: list[Box] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)
    candles: int = 0  # how many closed candles it was computed from
    swing_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def frame(candles: Sequence[Candle]) -> pd.DataFrame:
    """The library's input: lowercase open/high/low/close/volume columns, one row per candle, oldest first.
    The index is positional (the library indexes rows by position); times are kept in the `time` column."""
    return pd.DataFrame(
        {
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [float(c.volume) for c in candles],
            "time": [int(c.ts.timestamp()) for c in candles],
        }
    )


def _num(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if math.isnan(f) else f


def _idx(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None and f > 0 else None


def swings(ohlc: pd.DataFrame, swing_length: int) -> pd.DataFrame:
    shl = smc.swing_highs_lows(ohlc, swing_length=swing_length).copy()
    n = len(ohlc)
    if n:
        shl.loc[0, ["HighLow", "Level"]] = np.nan
        shl.loc[n - 1, ["HighLow", "Level"]] = np.nan
    return shl


def compute(candles: Sequence[Candle], swing_length: int = 5) -> Overlay:
    """The overlay for these closed candles (oldest first)."""
    out = Overlay(candles=len(candles), swing_length=swing_length)
    if len(candles) < 2 * swing_length + 3:
        return out
    ohlc = frame(candles)
    times = ohlc["time"].tolist()
    n = len(times)
    has_volume = bool((ohlc["volume"] > 0).any())

    def t(i: int | None) -> int | None:
        return times[i] if i is not None and 0 <= i < n else None

    # fair value gaps: drawn from the first of the three candles to the candle that filled them
    fvg = smc.fvg(ohlc)
    open_gaps: list[Box] = []
    filled: list[Box] = []
    for i in np.flatnonzero(fvg["FVG"].notna().to_numpy()):
        side: Side = "bull" if fvg["FVG"].iat[i] > 0 else "bear"
        top, bottom = _num(fvg["Top"].iat[i]), _num(fvg["Bottom"].iat[i])
        if top is None or bottom is None:
            continue
        end = _idx(fvg["MitigatedIndex"].iat[i])
        box = Box("fvg", side, max(top, bottom), min(top, bottom), times[max(i - 1, 0)], t(end), "FVG")
        (filled if end is not None else open_gaps).append(box)
    out.boxes += open_gaps[-MAX_FVG_OPEN:] + filled[-MAX_FVG_FILLED:]

    shl = swings(ohlc, swing_length)
    if shl["HighLow"].notna().sum() >= 2:
        # order blocks: from the block's candle to the candle that broke through it
        ob = smc.ob(ohlc, shl, close_mitigation=False)
        blocks: list[Box] = []
        for i in np.flatnonzero(ob["OB"].notna().to_numpy()):
            side = "bull" if ob["OB"].iat[i] > 0 else "bear"
            top, bottom = _num(ob["Top"].iat[i]), _num(ob["Bottom"].iat[i])
            if top is None or bottom is None:
                continue
            pct = _num(ob["Percentage"].iat[i])
            label = f"OB {pct:.0f}%" if has_volume and pct is not None else "OB"
            blocks.append(
                Box(
                    "ob",
                    side,
                    max(top, bottom),
                    min(top, bottom),
                    times[i],
                    t(_idx(ob["MitigatedIndex"].iat[i])),
                    label,
                )
            )
        out.boxes += blocks[-MAX_OB:]

        # structure: a dashed line at the broken swing level, from the swing to the candle that closed beyond it
        bc = smc.bos_choch(ohlc, shl, close_break=True)
        breaks: list[Line] = []
        for i in np.flatnonzero((bc["BOS"].notna() | bc["CHOCH"].notna()).to_numpy()):
            choch = bool(pd.notna(bc["CHOCH"].iat[i]))
            d = bc["CHOCH"].iat[i] if choch else bc["BOS"].iat[i]
            level = _num(bc["Level"].iat[i])
            broken = _idx(bc["BrokenIndex"].iat[i])
            if level is None or broken is None:
                continue
            kind: Literal["bos", "choch"] = "choch" if choch else "bos"
            breaks.append(
                Line(kind, "bull" if d > 0 else "bear", level, times[i], t(broken), "CHoCH" if choch else "BOS")
            )
        out.lines += breaks[-MAX_STRUCTURE:]

        # liquidity: equal highs / lows (resting stops), until swept
        liq = smc.liquidity(ohlc, shl)
        pools: list[Line] = []
        for i in np.flatnonzero(liq["Liquidity"].notna().to_numpy()):
            level = _num(liq["Level"].iat[i])
            if level is None:
                continue
            side = "bear" if liq["Liquidity"].iat[i] > 0 else "bull"  # equal highs: sell-side reaction expected
            swept = _idx(liq["Swept"].iat[i])
            label = "EQH" if liq["Liquidity"].iat[i] > 0 else "EQL"
            pools.append(Line("liquidity", side, level, times[i], t(swept), label + (" swept" if swept else "")))
        out.lines += pools[-MAX_LIQUIDITY:]

        # nearest confirmed swing on each side of the last close: the current support / resistance
        last = float(ohlc["close"].iat[-1])
        hl, lv = shl["HighLow"].to_numpy(), shl["Level"].to_numpy()
        highs = [float(lv[i]) for i in np.flatnonzero(hl == 1) if lv[i] > last]
        lows = [float(lv[i]) for i in np.flatnonzero(hl == -1) if lv[i] < last]
        if highs:
            out.levels.append(Level("resistance", min(highs), "Swing high"))
        if lows:
            out.levels.append(Level("support", max(lows), "Swing low"))

    # previous session's high and low
    daily = ohlc.set_index(pd.to_datetime(ohlc["time"], unit="s", utc=True).dt.tz_convert(IST))
    phl = smc.previous_high_low(daily, time_frame="1D")
    pdh, pdl = _num(phl["PreviousHigh"].iat[-1]), _num(phl["PreviousLow"].iat[-1])
    if pdh is not None:
        out.levels.append(Level("pdh", round(pdh, 2), "PDH"))
    if pdl is not None:
        out.levels.append(Level("pdl", round(pdl, 2), "PDL"))
    return out
