"""Shared hypothetical chart / paper signal DTO. Does not place broker orders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any


BUY = "BUY"
SELL = "SELL"


@dataclass
class ChartSignal:
    signal_id: str
    instrument: str
    timestamp: datetime
    signal_type: str
    option_type: str
    strike: float | None
    expiry: date | None
    option_ltp: float | None
    underlying_ltp: float | None
    strategy_score: float | None
    passed_conditions: int | None
    total_conditions: int | None
    entry_price: float | None
    target_price: float | None
    stop_loss_price: float | None
    exit_price: float | None
    exit_reason: str | None
    is_hypothetical: bool = True
    tradingsymbol: str = ""
    quantity: int | None = None
    gross_pnl: float | None = None
    total_charges: float | None = None
    net_pnl: float | None = None
    charges_estimated: bool | None = None
    source: str = "PAPER"
    paper_trade_id: int | None = None
    candle_low: float | None = None
    candle_high: float | None = None
    strategy_name: str = ""
    rejection_reason: str = ""
    result: str = "OPEN"
    candle_timestamp: str | None = None
    ema: float | None = None
    vwap: float | None = None
    rsi: float | None = None
    atr: float | None = None
    momentum: float | None = None
    ce_score: float | None = None
    pe_score: float | None = None
    threshold: float | None = None
    signal_reason: str = ""
    sequential: bool = False
    marker_role: str = "entry"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        stamp = self.timestamp
        payload["timestamp"] = stamp.isoformat() if isinstance(stamp, datetime) else stamp
        payload["expiry"] = self.expiry.isoformat() if isinstance(self.expiry, date) else self.expiry
        payload["time_unix"] = _unix(stamp) if isinstance(stamp, datetime) else None
        payload["note"] = "HYPOTHETICAL / PAPER signal. Not a guaranteed prediction. No broker order."
        return payload


def _unix(value: datetime) -> int:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(aware.timestamp())


def rising_edge(previous: bool, current: bool) -> bool:
    """First bar a side crosses the threshold. Later bars in the same run are not new signals."""
    return current and not previous
