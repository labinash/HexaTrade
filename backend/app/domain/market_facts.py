"""Shared last-bar indicator snapshot. Scoring stays in the engines that call this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.clock import to_ist
from app.domain.candles import Bar
from app.domain.indicators import atr, ema, rsi, session_vwap


@dataclass(frozen=True)
class MarketFacts:
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    ema20: float | None
    rsi: float | None
    atr: float | None
    vwap: float | None
    volume: float | None
    volume_ema: float | None
    bar_time: datetime | None
    timeframe: str


def market_facts_from_bars(
    bars: list[Bar],
    *,
    ema_period: int = 20,
    rsi_period: int = 14,
    atr_period: int = 14,
    volume_ema_period: int = 20,
    timeframe: str = "",
) -> MarketFacts:
    if not bars:
        return MarketFacts(None, None, None, None, None, None, None, None, None, None, None, timeframe)
    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    volumes = [float(bar.volume or 0) for bar in bars]
    last = bars[-1]
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    keys = [to_ist(bar.start).date() for bar in bars]
    return MarketFacts(
        close=closes[-1],
        open=float(last.open),
        high=highs[-1],
        low=lows[-1],
        ema20=ema(closes, ema_period)[-1],
        rsi=rsi(closes, rsi_period)[-1],
        atr=atr(highs, lows, closes, atr_period)[-1],
        vwap=session_vwap(typical, volumes, keys)[-1],
        volume=volumes[-1],
        volume_ema=ema(volumes, volume_ema_period)[-1],
        bar_time=last.start,
        timeframe=timeframe,
    )
