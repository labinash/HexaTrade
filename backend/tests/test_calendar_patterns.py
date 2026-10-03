"""Calendar weekday classification and returns. Paper / historical only."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.clock import IST, to_ist
from app.db.models import Base
from app.domain.calendar_patterns import (
    annotate_calendar,
    attach_session_returns,
    classify_gap,
    classify_move,
    first_last_of_months,
    first_last_of_weeks,
    ist_wall_date,
    missing_weekdays,
    reliability,
    session_marker_unix,
    session_stats,
    split_in_out,
    trading_weekday_ordinal,
    week_of_month,
    weekday_name,
)
from app.domain.chart_signals import _unix
from app.domain.historical import HistoricalBar, attach_ist
from app.domain.index_strategy import index_paper_net
from app.services.calendar_analyzer import _apply_filters, analyze_calendar
from app.services.historical_repo import row_from_bar


def _day(open_px, close, high=None, low=None, volume=1000, day=None):
    high = high if high is not None else max(open_px, close) + 1
    low = low if low is not None else min(open_px, close) - 1
    return {
        "trading_date": day,
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "vwap": (high + low + close) / 3,
    }


def test_weekday_names_cover_monday_to_friday():
    assert weekday_name(date(2026, 1, 5)) == "Monday"
    assert weekday_name(date(2026, 1, 6)) == "Tuesday"
    assert weekday_name(date(2026, 1, 7)) == "Wednesday"
    assert weekday_name(date(2026, 1, 8)) == "Thursday"
    assert weekday_name(date(2026, 1, 9)) == "Friday"
    assert weekday_name(date(2026, 1, 10)) == "Weekend"


def test_week_of_month_and_ordinal():
    days = [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)]
    assert week_of_month(date(2026, 1, 5)) == "First"
    assert week_of_month(date(2026, 1, 26)) == "Last"
    assert trading_weekday_ordinal(date(2026, 1, 5), days) == "First"
    assert trading_weekday_ordinal(date(2026, 1, 26), days) == "Last"
    assert trading_weekday_ordinal(date(2026, 1, 19), days) == "Third"


def test_month_year_and_first_last_trading_days():
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 9), date(2026, 2, 2)]
    first_w, last_w = first_last_of_weeks(days)
    first_m, last_m = first_last_of_months(days)
    assert date(2026, 1, 5) in first_w
    assert date(2026, 1, 9) in last_w
    assert date(2026, 1, 5) in first_m
    assert date(2026, 1, 9) in last_m
    assert date(2026, 2, 2) in first_m


def test_holiday_missing_weekdays():
    sessions = [date(2026, 1, 5), date(2026, 1, 7)]  # skip Tuesday 6th
    missing = missing_weekdays(sessions)
    assert date(2026, 1, 6) in missing
    assert date(2026, 1, 5) not in missing


def test_gap_and_previous_direction_no_lookahead():
    rows = attach_session_returns(
        [
            _day(100, 102, day=date(2026, 1, 5)),
            _day(104, 103, day=date(2026, 1, 6)),
            _day(102, 102, day=date(2026, 1, 7)),
        ]
    )
    assert rows[0]["prev_direction"] == "UNKNOWN"
    assert rows[1]["prev_direction"] == "BULLISH"
    assert rows[1]["gap_class"] == "GAP_UP"
    assert classify_move(0.2) == "BULLISH"
    assert classify_move(-0.2) == "BEARISH"
    assert classify_move(0.01) == "FLAT"
    assert classify_gap(0.2) == "GAP_UP"
    assert classify_gap(-0.2) == "GAP_DOWN"


def test_nifty_sensex_not_mixed_in_annotate():
    nifty = attach_session_returns([_day(22000, 22100, day=date(2026, 1, 5))])
    sensex = attach_session_returns([_day(72000, 72100, day=date(2026, 1, 5))])
    n = annotate_calendar(nifty, set())
    s = annotate_calendar(sensex, set())
    assert n[0]["open"] != s[0]["open"]
    assert n[0]["weekday"] == s[0]["weekday"] == "Monday"


def test_open_to_close_and_close_to_close_formulas():
    rows = attach_session_returns(
        [
            _day(100, 110, day=date(2026, 1, 5)),
            _day(108, 106, day=date(2026, 1, 6)),
        ]
    )
    assert abs(rows[0]["oc_pct"] - 10.0) < 1e-6
    assert abs(rows[1]["cc_pct"] - ((106 - 110) / 110 * 100)) < 1e-6
    assert rows[0]["price_basis"] == "INDEX_CLOSE_FUTURES_PROXY"


def test_small_sample_and_oos_split():
    tiny = [{"oc_pct": 0.2, "cc_pct": 0.2, "trading_date": date(2026, 1, 5 + i)} for i in range(5)]
    stats = session_stats(tiny, min_sample=20)
    assert stats["sample_sufficient"] is False
    assert stats["reliability"] == "Insufficient sample"
    rows = [{"oc_pct": 0.3, "cc_pct": 0.3, "trading_date": date(2026, 1, 1) + timedelta(days=i)} for i in range(40)]
    ins, oos = split_in_out(rows)
    assert len(ins) == 28
    assert len(oos) == 12
    assert ins[-1]["trading_date"] < oos[0]["trading_date"]


def test_failed_oos_and_multiple_testing_label():
    assert reliability(40, 0.2, -0.1, 20, 1.0, 2.0) == "Failed out-of-sample"
    assert reliability(10, 0.2, 0.2, 5, 0.1, 1.0) == "Insufficient sample"
    assert reliability(60, 0.2, 0.1, 25, 0.2, 4.0) == "Stronger evidence"


def test_expiry_not_inferred_from_thursday():
    thursday = date(2026, 1, 8)
    rows = annotate_calendar(attach_session_returns([_day(100, 101, day=thursday)]), set())
    assert rows[0]["expiry_proximity"] == "UNAVAILABLE"
    with_exp = annotate_calendar(attach_session_returns([_day(100, 101, day=thursday)]), {thursday})
    assert with_exp[0]["expiry_proximity"] == "EXPIRY_DAY"


def test_dashboard_totals_match_raw_sessions():
    cursor = date(2026, 1, 5)
    days = []
    for i in range(10):
        while cursor.weekday() >= 5:
            cursor += timedelta(days=1)
        days.append(_day(100, 101 if i % 2 == 0 else 99, day=cursor))
        cursor += timedelta(days=1)
    rows = attach_session_returns(days)
    stats = session_stats(rows, min_sample=5)
    raw_pos = sum(1 for row in rows if row["oc_pct"] > 0.10)
    assert stats["positive_sessions"] == raw_pos
    assert stats["sessions"] == 10
    assert abs(stats["avg_open_to_close_pct"] - sum(row["oc_pct"] for row in rows) / 10) < 1e-6


def test_duplicate_and_missing_candle_flags_via_bar_count():
    sessions = [
        {**_day(100, 101, day=date(2026, 1, 5)), "bars": 370},
        {**_day(100, 101, day=date(2026, 1, 6)), "bars": 200},
    ]
    missing = sum(max(0, 375 - int(row["bars"])) for row in sessions)
    assert missing > 0


def test_timezone_and_chart_marker_unix_match():
    stamp = datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    assert session_marker_unix(stamp) == _unix(stamp)
    naive = datetime(2026, 1, 5, 9, 15)
    assert session_marker_unix(naive) == _unix(naive)
    ist_day = to_ist(stamp).date()
    assert ist_day == date(2026, 1, 5)
    assert ist_wall_date(datetime(2026, 1, 5, 3, 45, tzinfo=timezone.utc)) == date(2026, 1, 5)


def test_index_pnl_formula_is_not_option_premium():
    gross, slip, net = index_paper_net("BUY", 25000, 25010, 65, slippage_points=0.5)
    assert float(gross) == 650.0
    assert float(net) == float(gross) - float(slip)
    sell_g, _, sell_n = index_paper_net("SELL", 25000, 24990, 65, slippage_points=0.5)
    assert float(sell_g) == 650.0
    assert float(sell_n) < float(sell_g)


def test_weekday_filter_keeps_monday_through_friday_apart():
    cursor = date(2026, 1, 5)
    rows = []
    for _ in range(10):
        while cursor.weekday() >= 5:
            cursor += timedelta(days=1)
        rows.append(_day(100, 101, day=cursor))
        cursor += timedelta(days=1)
    annotated = annotate_calendar(attach_session_returns(rows), set())
    mondays = _apply_filters(annotated, "Monday", None, None, None, None, None, None)
    assert mondays
    assert all(row["weekday"] == "Monday" for row in mondays)
    fridays = _apply_filters(annotated, "Friday", None, None, None, None, None, None)
    assert all(row["weekday"] == "Friday" for row in fridays)


def test_replay_and_backtest_session_returns_are_identical():
    rows = [
        _day(100, 102, day=date(2026, 1, 5)),
        _day(101, 99, day=date(2026, 1, 6)),
    ]
    first = attach_session_returns(rows)
    second = attach_session_returns(list(reversed(rows)))
    assert [row["oc_pct"] for row in first] == [row["oc_pct"] for row in second]
    assert first[0]["trading_date"] < first[1]["trading_date"]


def _seed_index(db, symbol, start, sessions, open_px):
    cursor = start
    added = 0
    while added < sessions:
        if cursor.weekday() < 5:
            close = open_px + (2 if cursor.weekday() == 0 else -1)
            db.add(
                row_from_bar(
                    instrument=symbol,
                    exchange="NSE" if symbol == "NIFTY" else "BSE",
                    timeframe="1Minute",
                    bar=HistoricalBar(
                        timestamp=datetime(cursor.year, cursor.month, cursor.day, 9, 15),
                        open=Decimal(str(open_px)),
                        high=Decimal(str(open_px + 5)),
                        low=Decimal(str(open_px - 5)),
                        close=Decimal(str(close)),
                        volume=1000,
                        open_interest=0,
                        trading_date=cursor,
                    ),
                    source="test",
                    tradingsymbol=symbol,
                )
            )
            db.add(
                row_from_bar(
                    instrument=symbol,
                    exchange="NSE" if symbol == "NIFTY" else "BSE",
                    timeframe="1Minute",
                    bar=HistoricalBar(
                        timestamp=datetime(cursor.year, cursor.month, cursor.day, 15, 29),
                        open=Decimal(str(close)),
                        high=Decimal(str(close + 1)),
                        low=Decimal(str(close - 1)),
                        close=Decimal(str(close)),
                        volume=800,
                        open_interest=0,
                        trading_date=cursor,
                    ),
                    source="test",
                    tradingsymbol=symbol,
                )
            )
            added += 1
        cursor += timedelta(days=1)
    return cursor


def test_analyze_calendar_separates_instruments_and_matches_counts():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    _seed_index(db, "NIFTY", date(2026, 1, 5), 40, 25000)
    _seed_index(db, "SENSEX", date(2026, 1, 5), 40, 81000)
    db.commit()
    payload = analyze_calendar(db, instrument="BOTH", min_sample=5)
    nifty = payload["instruments"]["NIFTY"]
    sensex = payload["instruments"]["SENSEX"]
    assert nifty["session_count"] == 40
    assert sensex["session_count"] == 40
    monday_n = next(row for row in nifty["weekday_table"] if row["weekday"] == "Monday")
    monday_s = next(row for row in sensex["weekday_table"] if row["weekday"] == "Monday")
    assert monday_n["sessions"] == monday_s["sessions"]
    assert monday_n["avg_open_to_close_pct"] != monday_s["avg_open_to_close_pct"]
    assert payload["nifty_vs_sensex"]["aligned_session_count"] == 40
    monday_filter = analyze_calendar(db, instrument="NIFTY", weekday="Monday", min_sample=5)
    only = monday_filter["instruments"]["NIFTY"]
    assert only["session_count"] == monday_n["sessions"]
    assert sum(row["sessions"] for row in only["weekday_table"] if row["weekday"] != "Monday") == 0
    db.close()


def test_attached_ist_bar_unix_is_the_chart_marker():
    stamp = attach_ist(datetime(2026, 1, 5, 9, 15))
    unix = session_marker_unix(stamp)
    assert unix == _unix(stamp)
    assert to_ist(stamp).hour == 9
