"""Alert-engine helpers that reuse existing EMA/ATR. No broker calls."""

from __future__ import annotations

from app.domain.candles import Bar
from app.domain.indicators import atr, ema


def ema_pair(closes: list[float], fast: int = 9, slow: int = 21) -> tuple[float | None, float | None]:
    fast_series = ema(closes, fast)
    slow_series = ema(closes, slow)
    return (fast_series[-1] if fast_series else None, slow_series[-1] if slow_series else None)


def supertrend_side(bars: list[Bar], period: int = 14, multiplier: float = 3.0) -> str | None:
    """ATR-based Supertrend. BULLISH when close is above the band, else BEARISH."""
    if len(bars) <= period:
        return None
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    closes = [bar.close for bar in bars]
    atr_series = atr(highs, lows, closes, period)
    upper = [None] * len(bars)
    lower = [None] * len(bars)
    trend = [None] * len(bars)
    direction = 1
    for i, bar in enumerate(bars):
        if atr_series[i] is None:
            continue
        mid = (bar.high + bar.low) / 2
        band_up = mid + multiplier * atr_series[i]
        band_dn = mid - multiplier * atr_series[i]
        if i == 0 or atr_series[i - 1] is None:
            upper[i] = band_up
            lower[i] = band_dn
            trend[i] = "BULLISH" if bar.close >= mid else "BEARISH"
            direction = 1 if trend[i] == "BULLISH" else -1
            continue
        prev_up = upper[i - 1] if upper[i - 1] is not None else band_up
        prev_dn = lower[i - 1] if lower[i - 1] is not None else band_dn
        upper[i] = band_up if bar.close > prev_up else min(band_up, prev_up)
        lower[i] = band_dn if bar.close < prev_dn else max(band_dn, prev_dn)
        if direction > 0:
            if bar.close < lower[i]:
                direction = -1
        else:
            if bar.close > upper[i]:
                direction = 1
        trend[i] = "BULLISH" if direction > 0 else "BEARISH"
    return trend[-1]


def swing_support_resistance(bars: list[Bar], wing: int = 3) -> tuple[float | None, float | None]:
    """Most recent swing high (resistance) and swing low (support) from visible bars only."""
    if len(bars) < wing * 2 + 1:
        return None, None
    resistance = None
    support = None
    last = len(bars) - wing - 1
    for i in range(wing, last + 1):
        high = bars[i].high
        low = bars[i].low
        if all(high >= bars[i - k].high and high >= bars[i + k].high for k in range(1, wing + 1)):
            resistance = high
        if all(low <= bars[i - k].low and low <= bars[i + k].low for k in range(1, wing + 1)):
            support = low
    return support, resistance
