"""Read/write MarketHistoricalData. Never writes to the live candle table."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import as_utc_naive, now_utc
from app.db.models import MarketHistoricalData, UnderlyingProfile
from app.domain.candles import Bar
from app.domain.historical import (
    INDEX_SENTINEL_EXPIRY,
    INDEX_SENTINEL_STRIKE,
    HistoricalBar,
    attach_ist,
    expected_session_bars,
)
from app.domain.paper_auto import OPTION_HISTORY_UNAVAILABLE


def get_historical_candles(
    db: Session,
    instrument: str,
    timeframe: str,
    from_date: date,
    to_date: date,
    *,
    contract_kind: str = "INDEX",
) -> list[MarketHistoricalData]:
    return list(
        db.scalars(
            select(MarketHistoricalData)
            .where(
                MarketHistoricalData.instrument == instrument,
                MarketHistoricalData.timeframe == timeframe,
                MarketHistoricalData.contract_kind == contract_kind,
                MarketHistoricalData.trading_date >= from_date,
                MarketHistoricalData.trading_date <= to_date,
            )
            .order_by(MarketHistoricalData.timestamp)
        ).all()
    )


def get_historical_candles_by_date(
    db: Session, instrument: str, timeframe: str, trading_date: date, *, contract_kind: str = "INDEX"
) -> list[MarketHistoricalData]:
    return get_historical_candles(db, instrument, timeframe, trading_date, trading_date, contract_kind=contract_kind)


def get_latest_historical_timestamp(
    db: Session, instrument: str, timeframe: str, *, contract_kind: str = "INDEX"
) -> datetime | None:
    return db.scalar(
        select(func.max(MarketHistoricalData.timestamp)).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == contract_kind,
        )
    )


def existing_timestamps(
    db: Session,
    instrument: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    contract_kind: str = "INDEX",
) -> set[datetime]:
    rows = db.scalars(
        select(MarketHistoricalData.timestamp).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == contract_kind,
            MarketHistoricalData.timestamp >= start,
            MarketHistoricalData.timestamp <= end,
        )
    ).all()
    return set(rows)


def insert_historical_data(db: Session, rows: list[MarketHistoricalData]) -> int:
    if not rows:
        return 0
    db.add_all(rows)
    db.flush()
    return len(rows)


def bars_from_rows(rows: list[MarketHistoricalData]) -> list[Bar]:
    out = []
    for row in rows:
        out.append(
            Bar(
                start=attach_ist(row.timestamp),
                open=float(row.open_price),
                high=float(row.high_price),
                low=float(row.low_price),
                close=float(row.close_price),
                volume=float(row.volume or 0),
                oi=float(row.open_interest or 0),
            )
        )
    return out


def stored_coverage(db: Session, timeframe: str = "1Minute") -> list[dict]:
    """Imported index range available for paper replay. Empty if nothing is stored."""
    out = []
    for symbol in ("NIFTY", "SENSEX"):
        first, last, candles, days = db.execute(
            select(
                func.min(MarketHistoricalData.trading_date),
                func.max(MarketHistoricalData.trading_date),
                func.count(),
                func.count(func.distinct(MarketHistoricalData.trading_date)),
            ).where(
                MarketHistoricalData.instrument == symbol,
                MarketHistoricalData.timeframe == timeframe,
                MarketHistoricalData.contract_kind == "INDEX",
            )
        ).one()
        out.append(
            {
                "instrument": symbol,
                "timeframe": timeframe,
                "from_date": first.isoformat() if first else None,
                "to_date": last.isoformat() if last else None,
                "candles": int(candles or 0),
                "trading_days": int(days or 0),
                "available": first is not None and last is not None and int(candles or 0) > 0,
            }
        )
    return out


def stored_dates(db: Session, instrument: str, timeframe: str = "1Minute") -> list[str]:
    rows = db.scalars(
        select(func.distinct(MarketHistoricalData.trading_date))
        .where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
        .order_by(MarketHistoricalData.trading_date.desc())
    ).all()
    return [row.isoformat() for row in rows if row]


def count_candles_in_range(db: Session, instrument: str, timeframe: str, from_date: date, to_date: date) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(MarketHistoricalData).where(
                MarketHistoricalData.instrument == instrument,
                MarketHistoricalData.timeframe == timeframe,
                MarketHistoricalData.contract_kind == "INDEX",
                MarketHistoricalData.trading_date >= from_date,
                MarketHistoricalData.trading_date <= to_date,
            )
        )
        or 0
    )


def stored_range(db: Session, instrument: str, timeframe: str) -> tuple[date, date] | None:
    first = db.scalar(
        select(func.min(MarketHistoricalData.trading_date)).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    )
    last = db.scalar(
        select(func.max(MarketHistoricalData.trading_date)).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    )
    if first is None or last is None:
        return None
    return first, last


def option_rows_exist(db: Session, instrument: str, timeframe: str, from_date: date, to_date: date) -> bool:
    token = db.scalar(
        select(MarketHistoricalData.id).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "OPTION",
            MarketHistoricalData.trading_date >= from_date,
            MarketHistoricalData.trading_date <= to_date,
        ).limit(1)
    )
    return token is not None


def quality_for(db: Session, instrument: str, timeframe: str, profile: UnderlyingProfile | None) -> dict:
    session_start = profile.session_start if profile else time(9, 15)
    session_end = profile.session_end if profile else time(15, 30)
    expected_per_day = expected_session_bars(session_start, session_end, timeframe)
    days = list(
        db.scalars(
            select(MarketHistoricalData.trading_date)
            .where(
                MarketHistoricalData.instrument == instrument,
                MarketHistoricalData.timeframe == timeframe,
                MarketHistoricalData.contract_kind == "INDEX",
            )
            .distinct()
        ).all()
    )
    imported = db.scalar(
        select(func.count()).select_from(MarketHistoricalData).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    ) or 0
    distinct_ts = db.scalar(
        select(func.count(func.distinct(MarketHistoricalData.timestamp))).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    ) or 0
    trading_days = len(days)
    expected = trading_days * expected_per_day
    missing = max(0, expected - int(imported))
    duplicates = max(0, int(imported) - int(distinct_ts))
    first = db.scalar(
        select(func.min(MarketHistoricalData.timestamp)).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    )
    last = db.scalar(
        select(func.max(MarketHistoricalData.timestamp)).where(
            MarketHistoricalData.instrument == instrument,
            MarketHistoricalData.timeframe == timeframe,
            MarketHistoricalData.contract_kind == "INDEX",
        )
    )
    incomplete = trading_days == 0 or missing > expected_per_day
    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "trading_days": trading_days,
        "expected_candles": expected,
        "imported_candles": int(imported),
        "candle_count": int(imported),
        "session_count": trading_days,
        "missing_candles": missing,
        "duplicate_timestamp_count": duplicates,
        "duplicate_candles": duplicates,
        "invalid_candles": 0,
        "first_timestamp": first.isoformat() if first else None,
        "last_timestamp": last.isoformat() if last else None,
        "available_from": first.date().isoformat() if first else None,
        "available_to": last.date().isoformat() if last else None,
        "incomplete": incomplete,
        "timezone": "Asia/Kolkata",
        "data_source": "market_historical_data INDEX",
        "price_type": "INDEX_CLOSE_FUTURES_PROXY",
        "option_history_available": False,
        "proxy_note": OPTION_HISTORY_UNAVAILABLE,
        "warning": "Historical data incomplete" if incomplete else OPTION_HISTORY_UNAVAILABLE,
        "stale": False,
    }


def row_from_bar(
    *,
    instrument: str,
    exchange: str | None,
    timeframe: str,
    bar: HistoricalBar,
    source: str,
    tradingsymbol: str,
    contract_kind: str = "INDEX",
    option_type: str = "",
    strike=INDEX_SENTINEL_STRIKE,
    expiry: date = INDEX_SENTINEL_EXPIRY,
) -> MarketHistoricalData:
    return MarketHistoricalData(
        instrument=instrument,
        exchange=exchange,
        timeframe=timeframe,
        trading_date=bar.trading_date,
        timestamp=bar.timestamp,
        open_price=bar.open,
        high_price=bar.high,
        low_price=bar.low,
        close_price=bar.close,
        volume=bar.volume,
        open_interest=bar.open_interest,
        data_source=source,
        contract_kind=contract_kind,
        option_type=option_type or "",
        strike=Decimal(str(strike or 0)),
        expiry=expiry or INDEX_SENTINEL_EXPIRY,
        tradingsymbol=tradingsymbol or instrument,
        created_date=as_utc_naive(now_utc()),
    )
