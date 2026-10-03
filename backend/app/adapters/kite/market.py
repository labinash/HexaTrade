import logging
import threading
from datetime import datetime, timedelta

from app.adapters.kite.client import call_with_backoff

log = logging.getLogger("hextrade.kite.market")


def fetch_quotes(kite, instruments: list[str]) -> dict:
    if not instruments:
        return {}
    # Kite accepts a few hundred keys per call. Stay in one batch for the chain window.
    chunk = instruments[:500]
    return call_with_backoff(kite.quote, chunk)


def fetch_historical(kite, token: int, start: datetime, end: datetime, interval: str, oi: bool = True) -> list[dict]:
    return call_with_backoff(
        kite.historical_data,
        token,
        start,
        end,
        interval,
        False,
        oi,
    )


def historical_chunks(start: datetime, end: datetime, interval: str) -> list[tuple[datetime, datetime]]:
    limits = {
        "minute": 60,
        "3minute": 100,
        "5minute": 100,
        "10minute": 100,
        "15minute": 100,
        "30minute": 100,
        "60minute": 400,
        "day": 2000,
    }
    span = limits.get(interval, 60)
    chunks = []
    cursor = start
    while cursor < end:
        nxt = min(cursor + timedelta(days=span), end)
        chunks.append((cursor, nxt))
        cursor = nxt
    return chunks


class Ticker:
    """Official KiteTicker thread. Quotes land in the shared cache via on_quote."""

    def __init__(self, api_key: str, access_token: str, on_quote) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.on_quote = on_quote
        self._ticker = None
        self._thread_started = False
        self.connected = False
        self._lock = threading.Lock()

    def start(self, tokens: list[int]) -> None:
        from kiteconnect import KiteTicker

        with self._lock:
            if self._ticker is None:
                ticker = KiteTicker(self.api_key, self.access_token)
                ticker.on_ticks = self._on_ticks
                ticker.on_connect = self._on_connect
                ticker.on_close = self._on_close
                ticker.on_error = self._on_error
                self._ticker = ticker
                ticker.connect(threaded=True)
                self._thread_started = True
            self._pending = tokens

    def subscribe(self, tokens: list[int]) -> None:
        ticker = self._ticker
        if ticker is None or not tokens:
            return
        from kiteconnect import KiteTicker

        ticker.subscribe(tokens)
        ticker.set_mode(ticker.MODE_FULL, tokens)

    def unsubscribe(self, tokens: list[int]) -> None:
        """Drop tokens the application no longer follows, so the subscription
        stays within the broker's per-connection limit as strikes move."""
        ticker = self._ticker
        if ticker is None or not tokens:
            return
        try:
            ticker.unsubscribe(tokens)
        except Exception:
            log.info("ticker unsubscribe failed")

    def stop(self) -> None:
        ticker = self._ticker
        self._ticker = None
        self.connected = False
        if ticker is not None:
            try:
                ticker.close()
            except Exception:
                log.info("ticker close failed")

    def _on_connect(self, ws, response) -> None:
        self.connected = True
        pending = getattr(self, "_pending", [])
        if pending:
            self.subscribe(pending)

    def _on_close(self, ws, code, reason) -> None:
        self.connected = False

    def _on_error(self, ws, code, reason) -> None:
        log.info("ticker error")
        self.connected = False

    def _on_ticks(self, ws, ticks) -> None:
        for tick in ticks:
            ohlc = tick.get("ohlc") or {}
            depth = tick.get("depth") or {}
            buy = (depth.get("buy") or [{}])[0]
            sell = (depth.get("sell") or [{}])[0]
            change = tick.get("change")
            if change is None and ohlc.get("close") is not None and tick.get("last_price") is not None:
                change = tick["last_price"] - ohlc["close"]
            self.on_quote(
                {
                    "token": tick.get("instrument_token"),
                    "ltp": tick.get("last_price"),
                    "volume": tick.get("volume_traded") or tick.get("volume"),
                    "oi": tick.get("oi"),
                    "change": change,
                    "bid": buy.get("price"),
                    "ask": sell.get("price"),
                    "bid_qty": buy.get("quantity"),
                    "ask_qty": sell.get("quantity"),
                    "open": ohlc.get("open"),
                    "high": ohlc.get("high"),
                    "low": ohlc.get("low"),
                    "close": ohlc.get("close"),
                    "timestamp": tick.get("exchange_timestamp") or tick.get("last_trade_time"),
                }
            )
