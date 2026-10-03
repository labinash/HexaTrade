"""Historical market-data provider contract. Implementations fetch; they do not write."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from app.domain.historical import HistoricalBar


class HistoricalMarketDataProvider(Protocol):
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
        """Return provider candles for one chunk. Empty list means no session that day."""
        ...


class ProviderError(RuntimeError):
    pass
