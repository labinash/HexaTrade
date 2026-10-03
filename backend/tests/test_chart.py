"""Chart signals reuse the strategy engine. Hypothetical / paper only."""

from datetime import date, datetime, timedelta

from app.core.clock import IST, now_ist
from app.domain.chart_signals import ChartSignal, rising_edge
from app.domain.dashboard_brief import best_setup_copy, pick_best
from app.domain.paper_auto import gated_sides, side_enabled
from app.domain.strategy import StrategyParams, make_bar, snapshot_from_bars
from app.services.chart_service import (
    HISTORICAL_ANALYSIS,
    LIVE_PAPER,
    MAX_SPAN_DAYS,
    _ist_in_range,
    _normalize_mode,
    _resolve_dates,
    _summary,
    series_from_bars,
)
from app.services.historical_backtest import _no_lookahead_window


def test_ce_off_drops_ce_from_new_entry_selection():
    ce = {"option_type": "CE", "score_percent": 88.0, "passed_count": 8, "atm_distance": 0}
    pe = {"option_type": "PE", "score_percent": 81.0, "passed_count": 9, "atm_distance": 0}
    gated_ce, gated_pe = gated_sides(ce, pe, False, True)
    assert gated_ce is None
    assert gated_pe is pe
    best, _ = pick_best([row for row in (gated_ce, gated_pe) if row], 75)
    assert best is pe


def test_pe_off_drops_pe_from_new_entry_selection():
    ce = {"option_type": "CE", "score_percent": 77.78, "passed_count": 7, "atm_distance": 0}
    pe = {"option_type": "PE", "score_percent": 90.0, "passed_count": 10, "atm_distance": 0}
    gated_ce, gated_pe = gated_sides(ce, pe, True, False)
    best, _ = pick_best([row for row in (gated_ce, gated_pe) if row], 75)
    assert best is ce


def test_side_off_means_no_new_entries_not_close():
    assert side_enabled(False, True, "CE") is False
    assert side_enabled(False, True, "PE") is True
    assert side_enabled(True, False, "PE") is False


def test_best_setup_requires_enabled_side_and_threshold():
    ce = {"option_type": "CE", "score_percent": 77.78, "passed_count": 7, "total_count": 9}
    pe = {"option_type": "PE", "score_percent": 81.82, "passed_count": 9, "total_count": 11}
    both = best_setup_copy(ce_enabled=True, pe_enabled=True, ce=ce, pe=pe, threshold=75)
    assert both["label"] == "BUY PE"
    ce_only = best_setup_copy(ce_enabled=True, pe_enabled=False, ce=ce, pe=pe, threshold=75)
    assert ce_only["label"] == "BUY CE"
    none = best_setup_copy(ce_enabled=True, pe_enabled=True, ce=ce, pe=pe, threshold=90)
    assert none["label"] == "NO TRADE"
    assert "75" not in none["reason"] or "90" in none["reason"]
    off = best_setup_copy(ce_enabled=False, pe_enabled=False, ce=ce, pe=pe, threshold=75)
    assert off["label"] == "NO TRADE"


def test_rising_edge_is_first_cross_only():
    assert rising_edge(False, True) is True
    assert rising_edge(True, True) is False
    assert rising_edge(True, False) is False
    assert rising_edge(False, False) is False


def test_indicator_series_matches_strategy_snapshot_last_values():
    bars = [
        make_bar(datetime(2026, 7, 15, 9, 15 + i, tzinfo=IST), 22500 + (i * 2), volume=1000 + i)
        for i in range(40)
    ]
    params = StrategyParams()
    snap = snapshot_from_bars(bars, params)
    _, series = series_from_bars(bars, params)
    assert round(series["ema20"][-1]["value"], 6) == round(snap.ema, 6)
    assert round(series["vwap"][-1]["value"], 6) == round(snap.vwap, 6)
    assert round(series["rsi"][-1]["value"], 6) == round(snap.rsi, 6)
    assert round(series["atr"][-1]["value"], 6) == round(snap.atr, 6)


def test_historical_signal_window_never_sees_future_bars():
    bars = [
        make_bar(datetime(2026, 7, 15, 9, 20 + i, tzinfo=IST), 22600 + i, volume=800)
        for i in range(12)
    ]
    visible = _no_lookahead_window(bars, 6)
    assert visible[-1] is bars[6]
    assert bars[7] not in visible
    assert bars[11] not in visible


def test_mode_aliases_keep_live_separate_from_historical():
    assert _normalize_mode("HISTORICAL") == HISTORICAL_ANALYSIS
    assert _normalize_mode("HISTORICAL_ANALYSIS") == HISTORICAL_ANALYSIS
    assert _normalize_mode("HISTORICAL_REPLAY") == "HISTORICAL_REPLAY"
    assert _normalize_mode("LIVE_PAPER") == LIVE_PAPER


def test_historical_dates_use_ist_store_and_cap_span():
    first, last = date(2023, 10, 2), date(2026, 10, 1)
    start, end, warnings = _resolve_dates(None, "2020-01-01", "2026-12-31", (first, last), HISTORICAL_ANALYSIS)
    assert start == first
    assert (end - start).days + 1 <= MAX_SPAN_DAYS
    assert warnings
    live_start, _, _ = _resolve_dates(None, None, None, (first, last), LIVE_PAPER)
    assert live_start == now_ist().date()


def test_ist_candle_filter_does_not_use_browser_timezone():
    assert _ist_in_range("2026-07-15 09:20:00", date(2026, 7, 15), date(2026, 7, 15)) is True
    assert _ist_in_range("2026-07-16 09:20:00", date(2026, 7, 15), date(2026, 7, 15)) is False


def test_resample_five_minute_from_one_minute_bars():
    from app.domain.candles import resample_bars
    from app.domain.strategy import make_bar

    bars = [
        make_bar(datetime(2026, 8, 1, 9, 15 + i, tzinfo=IST), 22000 + i, volume=100)
        for i in range(10)
    ]
    five = resample_bars(bars, 5)
    assert len(five) == 2
    assert five[0].open == bars[0].open
    assert five[0].close == bars[4].close
    assert five[0].high == max(row.high for row in bars[:5])
    assert five[1].open == bars[5].open


def test_resample_fifteen_minute_from_one_minute_bars():
    from app.domain.candles import resample_bars
    from app.domain.strategy import make_bar

    bars = [
        make_bar(datetime(2026, 8, 3, 9, 15 + i, tzinfo=IST), 22100 + i, volume=100)
        for i in range(30)
    ]
    fifteen = resample_bars(bars, 15)
    assert len(fifteen) == 2
    assert fifteen[0].open == bars[0].open
    assert fifteen[0].close == bars[14].close
    assert fifteen[1].open == bars[15].open


def test_chart_unix_is_utc_ist_label_is_kolkata():
    from datetime import timezone

    from app.domain.strategy import StrategyParams, make_bar

    bar = make_bar(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 22000)
    candles, _ = series_from_bars([bar], StrategyParams())
    assert candles[0]["ist"] == "2026-08-03 09:15:00"
    assert candles[0]["time"] == int(datetime(2026, 8, 3, 3, 45, tzinfo=timezone.utc).timestamp())


def test_timeframe_aliases_include_three_and_fifteen():
    from app.domain.historical import normalize_timeframe
    from app.services.chart_service import INTERVAL_MAP

    assert normalize_timeframe("3m") == "3Minute"
    assert normalize_timeframe("15m") == "15Minute"
    assert INTERVAL_MAP["15m"] == ("15minute", "15Minute")
    assert INTERVAL_MAP["3m"][1] == "3Minute"


def test_index_proxy_buy_marker_uses_buy_side_not_ce():
    from app.domain.chart_signals import ChartSignal
    from app.domain.index_strategy import config_from_dict, decide, features_from_bars
    from app.domain.strategy import make_bar

    bars = [
        make_bar(datetime(2025, 8, 12, 10, min(i, 59), tzinfo=IST), 24500 + i * 6, volume=1500 + i)
        for i in range(40)
    ]
    cfg = config_from_dict({"preset": "AGGRESSIVE", "buy_threshold": 10, "sell_threshold": 99, "sell_enabled": False})
    verdict = decide(features_from_bars(bars, cfg), cfg)
    assert verdict.decision in {"BUY", "HOLD"}
    stamp = datetime(2025, 8, 12, 10, 30, tzinfo=IST)
    marker = ChartSignal(
        signal_id="INDEX|NIFTY|BUY|ENTRY|1",
        instrument="NIFTY",
        timestamp=stamp,
        signal_type="BUY",
        option_type="BUY",
        strike=None,
        expiry=None,
        option_ltp=24600,
        underlying_ltp=24600,
        strategy_score=verdict.buy.score,
        passed_conditions=1,
        total_conditions=1,
        entry_price=24600,
        target_price=None,
        stop_loss_price=None,
        exit_price=None,
        exit_reason=None,
        is_hypothetical=True,
        source="INDEX_PROXY",
        marker_role="entry",
    )
    payload = marker.as_dict()
    assert payload["option_type"] == "BUY"
    assert payload["signal_type"] == "BUY"
    assert payload["source"] == "INDEX_PROXY"
    stamp = datetime(2026, 7, 15, 10, 0, tzinfo=IST)
    buy = ChartSignal(
        signal_id="1", instrument="NIFTY", timestamp=stamp, signal_type="BUY", option_type="CE",
        strike=22600, expiry=None, option_ltp=100, underlying_ltp=22600, strategy_score=80,
        passed_conditions=8, total_conditions=9, entry_price=100, target_price=None, stop_loss_price=None,
        exit_price=None, exit_reason=None,
    )
    sell = ChartSignal(
        signal_id="2", instrument="NIFTY", timestamp=stamp + timedelta(minutes=20), signal_type="SELL", option_type="PE",
        strike=22600, expiry=None, option_ltp=90, underlying_ltp=22580, strategy_score=82,
        passed_conditions=9, total_conditions=11, entry_price=110, target_price=None, stop_loss_price=None,
        exit_price=90, exit_reason="TIME_EXIT", net_pnl=-20,
    )
    summary = _summary([{"time": 1}], [buy, sell], [{"reason": "Score 40% below threshold 75%."}])
    assert summary["ce_buy"] == 1
    assert summary["pe_sell"] == 1
    assert summary["losing"] == 1
    assert summary["rejected"] == 1
    assert "not a guarantee" in summary["note"].lower()
