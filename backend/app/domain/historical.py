"""Historical market-data rules. No broker calls live here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.core.clock import IST, now_ist

LIVE = "LIVE"
HISTORICAL = "HISTORICAL"

INDEX_SENTINEL_EXPIRY = date(1900, 1, 1)
INDEX_SENTINEL_STRIKE = Decimal("0")

TIMEFRAME_TO_KITE = {
    "1Minute": "minute",
    "3Minute": "3minute",
    "5Minute": "5minute",
    "10Minute": "10minute",
    "15Minute": "15minute",
    "30Minute": "30minute",
    "60Minute": "60minute",
    "Day": "day",
}

KITE_TO_TIMEFRAME = {value: key for key, value in TIMEFRAME_TO_KITE.items()}

TIMEFRAME_MINUTES = {
    "1Minute": 1,
    "3Minute": 3,
    "5Minute": 5,
    "10Minute": 10,
    "15Minute": 15,
    "30Minute": 30,
    "60Minute": 60,
    "Day": 1440,
}


@dataclass(frozen=True)
class HistoricalBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_interest: int
    trading_date: date


def three_year_window(today: date | None = None) -> tuple[date, date]:
    end = today or now_ist().date()
    try:
        start = end.replace(year=end.year - 3)
    except ValueError:
        start = date(end.year - 3, 2, 28)
    return start, end


def normalize_timeframe(value: str | None) -> str:
    raw = (value or "1Minute").strip()
    if raw in TIMEFRAME_TO_KITE:
        return raw
    if raw in KITE_TO_TIMEFRAME:
        return KITE_TO_TIMEFRAME[raw]
    lowered = raw.lower().replace(" ", "")
    aliases = {
        "1min": "1Minute",
        "1m": "1Minute",
        "minute": "1Minute",
        "1minute": "1Minute",
        "3min": "3Minute",
        "3m": "3Minute",
        "3minute": "3Minute",
        "5min": "5Minute",
        "5m": "5Minute",
        "5minute": "5Minute",
        "15min": "15Minute",
        "15m": "15Minute",
        "15minute": "15Minute",
    }
    if lowered in aliases:
        return aliases[lowered]
    raise ValueError(f"Unsupported timeframe {value!r}. Record a known timeframe; do not mix resolutions.")


def kite_interval(timeframe: str) -> str:
    return TIMEFRAME_TO_KITE[normalize_timeframe(timeframe)]


def timeframe_minutes(timeframe: str) -> int:
    return TIMEFRAME_MINUTES[normalize_timeframe(timeframe)]


def as_ist_naive(value: datetime) -> datetime:
    """Wall-clock Asia/Kolkata without tzinfo. 09:20 stays 09:20."""
    if value.tzinfo is None:
        local = value.replace(tzinfo=IST)
    else:
        local = value.astimezone(IST)
    return local.replace(tzinfo=None, microsecond=0)


def attach_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def in_session(moment: datetime, session_start: time, session_end: time) -> bool:
    clock = attach_ist(moment).time().replace(microsecond=0)
    return session_start <= clock <= session_end


def is_weekend(moment: datetime | date) -> bool:
    day = moment.date() if isinstance(moment, datetime) else moment
    return day.weekday() >= 5


def validate_ohlc(open_px, high_px, low_px, close_px) -> str | None:
    o, h, l, c = Decimal(str(open_px)), Decimal(str(high_px)), Decimal(str(low_px)), Decimal(str(close_px))
    if h < o or h < c or h < l:
        return "High is below Open, Close or Low"
    if l > o or l > c or l > h:
        return "Low is above Open, Close or High"
    return None


def expected_session_bars(session_start: time, session_end: time, timeframe: str) -> int:
    minutes = timeframe_minutes(timeframe)
    if minutes <= 0 or minutes >= 1440:
        return 1
    start_m = session_start.hour * 60 + session_start.minute
    end_m = session_end.hour * 60 + session_end.minute
    return max(0, (end_m - start_m) // minutes)


def parse_instruments(value) -> list[str]:
    if value is None or value == "" or str(value).upper() == "BOTH":
        return ["NIFTY", "SENSEX"]
    if isinstance(value, (list, tuple)):
        rows = [str(item).upper() for item in value]
    else:
        rows = [part.strip().upper() for part in str(value).split(",")]
    out = []
    for item in rows:
        if item and item not in out:
            out.append(item)
    if not out:
        raise ValueError("Select NIFTY, SENSEX or Both")
    return out


def missing_ranges(latest: datetime | None, start: date, end: date) -> tuple[date, date]:
    """Incremental window: after the last stored candle through end. Empty if already current."""
    if latest is None:
        return start, end
    local = as_ist_naive(latest) if isinstance(latest, datetime) else datetime.combine(latest, time(0, 0))
    nxt = local.date()
    if local.time() >= time(15, 29):
        nxt = local.date() + timedelta(days=1)
    if nxt > end:
        return end, end
    return max(start, nxt), end
