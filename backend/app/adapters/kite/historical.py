"""Kite implementation of historical bars. Does not persist and does not place orders."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from app.adapters.kite.market import fetch_historical, historical_chunks
from app.core.clock import IST, combine_ist
from app.domain.historical import HistoricalBar, as_ist_naive, kite_interval
from app.domain.historical_provider import ProviderError


class KiteHistoricalMarketDataProvider:
    source = "KiteHistorical"

    def __init__(self, kite) -> None:
        self.kite = kite

    def get_historical_data(
        self,
        *,
        instrument: str,
        exchange: str,
        timeframe: str,
        from_date: date,
        to_date: date,
        token: int,
    ) -> list[HistoricalBar]:
        interval = kite_interval(timeframe)
        start = combine_ist(from_date, time(9, 0))
        end = combine_ist(to_date, time(16, 0))
        if end <= start:
            return []
        rows: list[HistoricalBar] = []
        try:
            raw = fetch_historical(self.kite, token, start, end, interval, True)
        except Exception as exc:
            raise ProviderError(f"{instrument} {from_date}–{to_date}: {type(exc).__name__}") from exc
        for item in raw or []:
            moment = item.get("date")
            if moment is None:
                continue
            if isinstance(moment, datetime):
                stamp = as_ist_naive(moment)
            else:
                continue
            rows.append(
                HistoricalBar(
                    timestamp=stamp,
                    open=Decimal(str(item["open"])),
                    high=Decimal(str(item["high"])),
                    low=Decimal(str(item["low"])),
                    close=Decimal(str(item["close"])),
                    volume=int(item.get("volume") or 0),
                    open_interest=int(item.get("oi") or 0),
                    trading_date=stamp.date(),
                )
            )
        return rows


def chunk_windows(from_date: date, to_date: date, timeframe: str) -> list[tuple[date, date]]:
    interval = kite_interval(timeframe)
    start = datetime.combine(from_date, time(0, 0), tzinfo=IST)
    end = datetime.combine(to_date, time(23, 59), tzinfo=IST)
    out = []
    for left, right in historical_chunks(start, end, interval):
        out.append((left.date(), right.date()))
    return out or [(from_date, to_date)]
