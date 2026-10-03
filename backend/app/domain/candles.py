from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float


def bucket_start(moment: datetime, minutes: int) -> datetime:
    if minutes <= 0:
        raise ValueError("timeframe must be positive")
    day_floor = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((moment - day_floor).total_seconds() // 60)
    snapped = (elapsed // minutes) * minutes
    return day_floor + timedelta(minutes=snapped)


def resample_bars(bars: list[Bar], minutes: int) -> list[Bar]:
    """Build higher-timeframe OHLC from 1-minute bars. Does not mix live candles."""
    if minutes <= 1 or not bars:
        return list(bars)
    groups: dict[datetime, list[Bar]] = {}
    order: list[datetime] = []
    for bar in bars:
        key = bucket_start(bar.start, minutes)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(bar)
    out: list[Bar] = []
    for key in order:
        group = groups[key]
        out.append(
            Bar(
                start=key,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
                oi=group[-1].oi,
            )
        )
    return out


class CandleAggregator:
    """Builds closed bars from ticks. The forming bar is not emitted."""

    def __init__(self, minutes: int = 1) -> None:
        self.minutes = minutes
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._volume_base: float | None = None
        self._oi: float = 0
        self._bucket: datetime | None = None
        self._last_volume: float = 0

    def update(self, moment: datetime, price: float, day_volume: float, oi: float) -> Bar | None:
        start = bucket_start(moment, self.minutes)
        closed: Bar | None = None
        if self._bucket is None:
            self._start_bucket(start, price, day_volume, oi)
            return None
        if start != self._bucket:
            closed = self._close_bucket()
            self._start_bucket(start, price, day_volume, oi)
            return closed
        self._high = price if self._high is None else max(self._high, price)
        self._low = price if self._low is None else min(self._low, price)
        self._close = price
        self._oi = oi
        self._last_volume = day_volume
        return None

    def _start_bucket(self, start: datetime, price: float, day_volume: float, oi: float) -> None:
        self._bucket = start
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._volume_base = day_volume
        self._last_volume = day_volume
        self._oi = oi

    def _close_bucket(self) -> Bar | None:
        if self._bucket is None or self._open is None or self._close is None:
            return None
        volume = max(0.0, self._last_volume - (self._volume_base or 0))
        return Bar(
            start=self._bucket,
            open=self._open,
            high=self._high or self._close,
            low=self._low or self._close,
            close=self._close,
            volume=volume,
            oi=self._oi,
        )
