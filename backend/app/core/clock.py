from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.astimezone(UTC)


def as_utc_naive(value: datetime) -> datetime:
    return to_utc(value).replace(tzinfo=None)


def session_expiry(login_at: datetime) -> datetime:
    """Kite access tokens expire at 06:00 IST on the next session boundary."""
    local = to_ist(login_at)
    six = time(6, 0)
    if local.time() < six:
        expiry_day = local.date()
    else:
        expiry_day = local.date() + timedelta(days=1)
    expiry = datetime.combine(expiry_day, six, tzinfo=IST)
    return expiry.astimezone(UTC)


def within_minutes_of_expiry(expires_at: datetime, minutes: int = 30) -> bool:
    remaining = to_utc(expires_at) - now_utc()
    return timedelta(0) < remaining <= timedelta(minutes=minutes)


def is_expired(expires_at: datetime) -> bool:
    return to_utc(expires_at) <= now_utc()


def combine_ist(day, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=IST)


def minutes_from_open(clock: time, session_open: time = time(9, 15)) -> int:
    return (clock.hour * 60 + clock.minute) - (session_open.hour * 60 + session_open.minute)


def reference_aligned(clock: time, timeframe_minutes: int, session_open: time = time(9, 15)) -> bool:
    if timeframe_minutes <= 0:
        return False
    delta = minutes_from_open(clock, session_open)
    return delta >= 0 and delta % timeframe_minutes == 0
