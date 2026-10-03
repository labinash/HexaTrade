"""Pin EMA20 / RSI14 / ATR14 on three stored sessions before any later indicator edits."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.domain.indicators import atr, ema, rsi
from app.domain.strategy import StrategyParams, make_bar
from app.services.chart_service import series_from_bars
from app.core.clock import IST
from datetime import datetime

PINS = {
    "2024-06-04": {"ema20": 21932.479, "rsi": 54.582, "atr": 85.366, "candles": 125},
    "2025-08-28": {"ema20": 24518.133, "rsi": 53.679, "atr": 18.449, "candles": 125},
    "2025-09-02": {"ema20": 24583.107, "rsi": 44.597, "atr": 19.740, "candles": 125},
}


def _chart(day: str) -> dict:
    url = (
        "http://127.0.0.1:8000/api/chart?instrument=NIFTY&timeframe=3m"
        f"&mode=HISTORICAL_ANALYSIS&start={day}&end={day}&range=1D"
    )
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.loads(response.read().decode())


@pytest.mark.parametrize("day", sorted(PINS))
def test_archive_chart_pins_ema_rsi_atr(day):
    try:
        payload = _chart(day)
    except (urllib.error.URLError, TimeoutError, OSError):
        pytest.skip("chart API not running")
    pin = PINS[day]
    assert payload["data_status"] == "OK"
    assert payload["date_from"] == day
    assert len(payload["candles"]) == pin["candles"]
    ema_v = payload["indicators"]["ema20"][-1]["value"]
    rsi_v = payload["indicators"]["rsi"][-1]["value"]
    atr_v = payload["indicators"]["atr"][-1]["value"]
    assert abs(ema_v - pin["ema20"]) < 0.02
    assert abs(rsi_v - pin["rsi"]) < 0.02
    assert abs(atr_v - pin["atr"]) < 0.02


def test_shared_facts_match_series_from_bars_last_point():
    from app.domain.market_facts import market_facts_from_bars

    bars = [
        make_bar(datetime(2024, 6, 4, 9, 15 + i, tzinfo=IST), 22000 + i * 3, volume=1000 + i)
        for i in range(40)
    ]
    params = StrategyParams()
    _, series = series_from_bars(bars, params)
    facts = market_facts_from_bars(bars, ema_period=20, rsi_period=14, atr_period=14)
    assert round(facts.ema20, 6) == round(series["ema20"][-1]["value"], 6)
    assert round(facts.rsi, 6) == round(series["rsi"][-1]["value"], 6)
    assert round(facts.atr, 6) == round(series["atr"][-1]["value"], 6)
    closes = [bar.close for bar in bars]
    assert ema(closes, 20)[-1] == facts.ema20
    assert rsi(closes, 14)[-1] == facts.rsi
