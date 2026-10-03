"""Dashboard, alert, date, and futures P&L audit. Paper only — no broker orders."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.clock import IST, as_utc_naive, to_ist
from app.db.models import Base
from app.domain.alert_engine import in_cooldown, public_status
from app.domain.historical import as_ist_naive, parse_instruments, three_year_window, validate_ohlc
from app.domain.index_strategy import (
    index_paper_net,
    performance_from_trades,
    signed_index_gross,
)
from app.domain.paper_auto import (
    OPTION_HISTORY_UNAVAILABLE,
    PNL_FORMULA,
    futures_gross,
    futures_net,
    slippage_cost,
)
from app.domain.strategy import make_bar
from app.services.chart_service import _resolve_dates, _summary
from app.services.historical_backtest import _index_gross, _no_lookahead_window
from app.services.historical_repo import quality_for, row_from_bar
from app.domain.historical import HistoricalBar
from app.domain.chart_signals import ChartSignal


def test_valid_three_year_window():
    start, end = three_year_window(date(2026, 10, 2))
    assert start == date(2023, 10, 2)
    assert end == date(2026, 10, 2)


def test_invalid_date_range_is_rejected_by_import_helper():
    from app.services.historical_import import queue_import

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        queue_import(db, instruments="NIFTY", date_from=date(2026, 10, 2), date_to=date(2026, 10, 1))
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "before" in str(exc).lower()
    db.close()


def test_date_boundary_inclusive_filter_uses_trading_date():
    from app.services.historical_repo import get_historical_candles

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    for day, hour in ((date(2026, 1, 5), 9), (date(2026, 1, 6), 10), (date(2026, 1, 7), 11)):
        db.add(
            row_from_bar(
                instrument="NIFTY",
                exchange="NSE",
                timeframe="1Minute",
                bar=HistoricalBar(
                    timestamp=datetime(day.year, day.month, day.day, hour, 15),
                    open=Decimal("25000"),
                    high=Decimal("25001"),
                    low=Decimal("24999"),
                    close=Decimal("25000.5"),
                    volume=100,
                    open_interest=0,
                    trading_date=day,
                ),
                source="test",
                tradingsymbol="NIFTY",
            )
        )
    db.commit()
    rows = get_historical_candles(db, "NIFTY", "1Minute", date(2026, 1, 5), date(2026, 1, 6))
    assert [row.trading_date for row in rows] == [date(2026, 1, 5), date(2026, 1, 6)]
    db.close()


def test_nifty_and_sensex_filters_stay_apart():
    assert parse_instruments("NIFTY") == ["NIFTY"]
    assert parse_instruments("SENSEX") == ["SENSEX"]
    assert "NIFTY" in parse_instruments("BOTH") and "SENSEX" in parse_instruments("BOTH")


def test_timezone_ist_wall_clock():
    stamp = datetime(2026, 1, 15, 9, 15, tzinfo=IST)
    naive = as_ist_naive(stamp)
    assert naive.hour == 9
    assert naive.minute == 15
    assert to_ist(stamp).tzinfo is not None


def test_missing_and_duplicate_quality():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    empty = quality_for(db, "NIFTY", "1Minute", None)
    assert empty["candle_count"] == 0
    assert empty["session_count"] == 0
    assert empty["timezone"] == "Asia/Kolkata"
    assert empty["option_history_available"] is False
    assert OPTION_HISTORY_UNAVAILABLE in empty["proxy_note"]
    db.close()


def test_buy_and_sell_futures_pnl_formula():
    buy_gross = futures_gross("BUY", 25000, 25010, 65)
    sell_gross = futures_gross("SELL", 25000, 24900, 65)
    assert buy_gross == Decimal("650.00")
    assert sell_gross == Decimal("6500.00")
    slip = slippage_cost(65, 0.5)
    assert slip == Decimal("32.50")
    assert signed_index_gross("BUY", 25000, 25010, 65, 0.5) == buy_gross
    gross, slip_r, net = index_paper_net("SELL", 25279.10, 25290.20, 65, slippage_points=0.5)
    assert gross == Decimal("-721.50")
    assert net == futures_net(gross, slippage=slip_r)
    assert "NET = gross" in PNL_FORMULA


def test_option_stt_is_not_applied_inside_index_net():
    gross, slip, net = index_paper_net("BUY", 25000, 25010, 65, slippage_points=0.5)
    assert gross - slip == net
    assert slip < Decimal("100")
    assert net > Decimal("500")


def test_win_rate_drawdown_daily_pnl_match_raw_rows():
    rows = [
        {"status": "CLOSED", "net_pnl": 100, "gross_pnl": 120, "total_charges": 20, "brokerage": 10, "slippage": 10, "option_type": "BUY", "instrument": "NIFTY", "exit_reason": "TARGET_HIT", "date": "2026-01-15", "exit_time": "a"},
        {"status": "CLOSED", "net_pnl": -40, "gross_pnl": -30, "total_charges": 10, "brokerage": 5, "slippage": 5, "option_type": "SELL", "instrument": "SENSEX", "exit_reason": "STOP_LOSS_HIT", "date": "2026-01-16", "exit_time": "b"},
        {"status": "OPEN", "net_pnl": 0, "gross_pnl": 0, "option_type": "BUY", "instrument": "NIFTY"},
    ]
    summary = performance_from_trades(rows)
    assert summary["total_trades"] == 3
    assert summary["closed_trades"] == 2
    assert summary["open_trades"] == 1
    assert summary["buy_trades"] == 2
    assert summary["sell_trades"] == 1
    assert summary["winning_trades"] == 1
    assert summary["losing_trades"] == 1
    assert summary["win_rate"] == 50.0
    assert summary["net_pnl"] == 60
    assert summary["nifty_pnl"] == 100
    assert summary["sensex_pnl"] == -40
    assert summary["max_drawdown"] >= 0
    assert summary["daily_pnl"][0]["daily_pnl"] == 100
    assert summary["average_trade_result"] == 30.0
    assert "NOT LIVE" in summary["label"]


def test_hold_and_threshold_and_cooldown():
    from app.domain.index_strategy import config_from_dict, decide, features_from_bars

    cfg = config_from_dict({"buy_threshold": 99, "sell_threshold": 99})
    bars = [make_bar(datetime(2026, 7, 15, 10, i, tzinfo=IST), 25000 + i, volume=1000) for i in range(20)]
    verdict = decide(features_from_bars(bars, cfg), cfg)
    assert verdict.decision == "HOLD"
    now = datetime(2026, 7, 15, 10, 30, tzinfo=IST)
    last = now - timedelta(seconds=30)
    assert in_cooldown(last, now, 300, True)
    assert not in_cooldown(last, now, 300, False)


def test_alert_status_codes():
    assert public_status("NEW") == "NEW"
    assert public_status("ACTIVE") == "ACTIVE"
    assert public_status("TARGET 1 HIT") == "TARGET_HIT"
    assert public_status("STOP-LOSS HIT") == "STOP_LOSS_HIT"
    assert public_status("WAIT", blocked=True) == "REJECTED"
    assert public_status("EXPIRED") == "EXPIRED"
    assert public_status("CANCELLED") == "CANCELLED"


def test_chart_marker_summary_drawdown_and_empty():
    stamp = datetime(2026, 7, 15, 10, 0, tzinfo=IST)
    buy = ChartSignal(
        signal_id="1", instrument="NIFTY", timestamp=stamp, signal_type="BUY", option_type="BUY",
        strike=None, expiry=None, option_ltp=25000, underlying_ltp=25000, strategy_score=80,
        passed_conditions=6, total_conditions=7, entry_price=25000, target_price=None, stop_loss_price=None,
        exit_price=None, exit_reason=None, marker_role="entry",
    )
    sell = ChartSignal(
        signal_id="2", instrument="NIFTY", timestamp=stamp + timedelta(minutes=15), signal_type="SELL", option_type="BUY",
        strike=None, expiry=None, option_ltp=24990, underlying_ltp=24990, strategy_score=80,
        passed_conditions=6, total_conditions=7, entry_price=25000, target_price=None, stop_loss_price=None,
        exit_price=24990, exit_reason="STOP_LOSS_HIT", net_pnl=-650, gross_pnl=-650, marker_role="exit",
    )
    summary = _summary([{"time": 1}, {"time": 2}], [buy, sell], [])
    assert summary["buy"] == 1
    assert summary["losing"] == 1
    assert summary["max_drawdown"] == 650
    empty = _summary([], [], [])
    assert empty["win_rate"] is None
    assert empty["net_pnl"] == 0


def test_chart_date_swap_and_empty_cover():
    start, end, warnings = _resolve_dates("2026-02-02", "2026-02-10", "2026-02-01", (date(2026, 1, 1), date(2026, 3, 1)), "HISTORICAL_ANALYSIS")
    assert start <= end
    assert any("swapped" in item.lower() for item in warnings)
    start2, end2, more = _resolve_dates(None, "2020-01-01", "2020-01-02", (date(2023, 10, 3), date(2026, 10, 1)), "HISTORICAL_ANALYSIS")
    assert start2 >= date(2023, 10, 3)


def test_no_lookahead_and_replay_same_window():
    bars = [make_bar(datetime(2026, 7, 15, 9, 15 + i, tzinfo=IST), 22500 + i, volume=100) for i in range(8)]
    visible = _no_lookahead_window(bars, 3)
    assert visible[-1] is bars[3]
    assert bars[4] not in visible


def test_index_short_matches_entry_minus_exit():
    assert _index_gross("SELL", Decimal("25000"), Decimal("24900"), 65) == Decimal("6500.00")
    assert _index_gross("BUY", Decimal("25000"), Decimal("25100"), 65) == Decimal("6500.00")


def test_ohlc_invalid_is_rejected():
    assert validate_ohlc(100, 99, 98, 100) is not None
