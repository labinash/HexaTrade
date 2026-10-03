"""Historical import and replay rules. No broker orders."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.clock import IST
from app.db.models import Base
from app.domain.historical import (
    as_ist_naive,
    missing_ranges,
    parse_instruments,
    three_year_window,
    validate_ohlc,
)
from app.services.historical_backtest import _no_lookahead_window
from app.services.historical_repo import existing_timestamps, insert_historical_data, row_from_bar
from app.domain.historical import HistoricalBar
from app.domain.strategy import make_bar


def test_three_year_window_is_computed_not_hardcoded():
    start, end = three_year_window(date(2026, 10, 2))
    assert end == date(2026, 10, 2)
    assert start == date(2023, 10, 2)


def test_three_year_window_handles_leap_day():
    start, end = three_year_window(date(2028, 2, 29))
    assert end == date(2028, 2, 29)
    assert start == date(2025, 2, 28)


def test_parse_instruments_keeps_nifty_and_sensex_apart():
    assert parse_instruments("BOTH") == ["NIFTY", "SENSEX"]
    assert parse_instruments("NIFTY") == ["NIFTY"]
    assert parse_instruments(["SENSEX"]) == ["SENSEX"]


def test_ohlc_validation_rejects_corrupt_bars():
    assert validate_ohlc(100, 101, 99, 100.5) is None
    assert validate_ohlc(100, 99, 98, 100) is not None
    assert validate_ohlc(100, 110, 105, 108) is not None


def test_incremental_range_starts_after_the_last_stored_candle():
    latest = datetime(2026, 9, 30, 15, 29)
    start, end = missing_ranges(latest, date(2023, 10, 2), date(2026, 10, 2))
    assert start == date(2026, 10, 1)
    assert end == date(2026, 10, 2)


def test_ist_wall_clock_is_not_shifted_to_utc():
    stamp = datetime(2026, 9, 30, 9, 20, tzinfo=IST)
    naive = as_ist_naive(stamp)
    assert naive.hour == 9
    assert naive.minute == 20


def test_replay_window_never_includes_future_bars():
    bars = [
        make_bar(datetime(2026, 7, 15, 9, 20, tzinfo=IST), 22500 + i, volume=1000)
        for i in range(10)
    ]
    visible = _no_lookahead_window(bars, 4)
    assert visible[-1] is bars[4]
    assert bars[5] not in visible


def test_insert_skips_duplicate_instrument_timestamp_timeframe():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    bar = HistoricalBar(
        timestamp=datetime(2026, 9, 30, 9, 20),
        open=Decimal("22500.25"),
        high=Decimal("22520.50"),
        low=Decimal("22490.10"),
        close=Decimal("22515.75"),
        volume=125000,
        open_interest=0,
        trading_date=date(2026, 9, 30),
    )
    row = row_from_bar(instrument="NIFTY", exchange="NSE", timeframe="1Minute", bar=bar, source="KiteHistorical", tradingsymbol="NIFTY 50")
    insert_historical_data(db, [row])
    db.commit()
    known = existing_timestamps(db, "NIFTY", "1Minute", datetime(2026, 9, 30, 9, 0), datetime(2026, 9, 30, 10, 0))
    assert datetime(2026, 9, 30, 9, 20) in known
    db.close()


def test_paper_range_must_use_stored_historical_dates():
    from app.services.historical_backtest import queue_run
    from app.services.historical_repo import stored_coverage, stored_range

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    bar = HistoricalBar(
        timestamp=datetime(2026, 9, 30, 9, 20),
        open=Decimal("22500"),
        high=Decimal("22520"),
        low=Decimal("22490"),
        close=Decimal("22515"),
        volume=1,
        open_interest=0,
        trading_date=date(2026, 9, 30),
    )
    insert_historical_data(db, [row_from_bar(instrument="NIFTY", exchange="NSE", timeframe="1Minute", bar=bar, source="test", tradingsymbol="NIFTY 50")])
    db.commit()
    assert stored_range(db, "NIFTY", "1Minute") == (date(2026, 9, 30), date(2026, 9, 30))
    nifty = next(row for row in stored_coverage(db) if row["instrument"] == "NIFTY")
    assert nifty["available"] is True
    try:
        queue_run(db, instrument="NIFTY", date_from=date(2023, 1, 1), date_to=date(2023, 1, 2))
        raise AssertionError("expected range error")
    except ValueError as error:
        assert "outside stored" in str(error)
    three = queue_run(
        db,
        instrument="NIFTY",
        date_from=date(2026, 9, 30),
        date_to=date(2026, 9, 30),
        timeframe="3Minute",
    )
    assert three.timeframe == "3Minute"
    assert three.date_from == date(2026, 9, 30)
    db.close()


def test_available_score_skips_unavailable_conditions():
    from app.domain.option_buy import (
        FAIL,
        NO_TRADE,
        PASS,
        UNAVAILABLE,
        ConditionResult,
        OptionStrategyResult,
        available_score_percent,
    )

    def cond(name: str, status: str) -> ConditionResult:
        return ConditionResult(
            name=name,
            current="",
            required="",
            passed=status == PASS,
            status=status,
            failure_reason="",
        )

    result = OptionStrategyResult(
        strategy="CE BUY",
        status=NO_TRADE,
        passed=False,
        passed_count=2,
        total_count=9,
        conditions=[
            cond("premium", UNAVAILABLE),
            cond("volume", PASS),
            cond("vwap", PASS),
            cond("rsi", FAIL),
        ],
    )
    assert available_score_percent(result) == 66.67


def test_index_futures_short_pnl_is_entry_minus_exit():
    from types import SimpleNamespace

    from app.services.historical_backtest import _index_gross, _paper_mode

    long_pnl = _index_gross("BUY", Decimal("25000"), Decimal("25100"), 65)
    short_pnl = _index_gross("SELL", Decimal("25000"), Decimal("24900"), 65)
    assert long_pnl > 0
    assert short_pnl > 0
    assert _paper_mode(SimpleNamespace(option_data_available=False, option_data_note="Kite does not provide")) == "INDEX_FUTURES_PROXY"
    assert _paper_mode(SimpleNamespace(option_data_available=True, option_data_note="INDEX FUTURES PROXY")) == "INDEX_FUTURES_PROXY"
    from app.services.historical_import import INDEX_FUTURES_NOTE, OPTION_NOTE
    assert "INDEX FUTURES PROXY" in INDEX_FUTURES_NOTE
    assert "NIFTY" in OPTION_NOTE and "SENSEX" in OPTION_NOTE


def test_repair_strips_option_charges_from_index_proxy_rows():
    from app.db.models import HistoricalPaperRun, HistoricalPaperTrade
    from app.services.historical_backtest import repair_index_proxy_charges

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    now = datetime(2026, 1, 30, 10, 0)
    run = HistoricalPaperRun(
        created_at=now,
        updated_at=now,
        status="COMPLETED",
        instrument="NIFTY",
        timeframe="1Minute",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 30),
        total_trades=2,
        wins=0,
        losses=2,
        gross_pnl=Decimal("1576.25"),
        total_charges=Decimal("9000"),
        net_pnl=Decimal("-7424"),
    )
    db.add(run)
    db.flush()
    db.add(
        HistoricalPaperTrade(
            run_id=run.id,
            trade_date=date(2026, 1, 29),
            entry_time=now,
            exit_time=now,
            instrument="NIFTY",
            option_type="SELL",
            quantity=65,
            entry_price=Decimal("25224.85"),
            exit_price=Decimal("25200.60"),
            signal_percent=Decimal("70"),
            gross_pnl=Decimal("1576.25"),
            total_charges=Decimal("4520.16"),
            net_pnl=Decimal("-2943.91"),
            exit_reason="TARGET",
        )
    )
    db.add(
        HistoricalPaperTrade(
            run_id=run.id,
            trade_date=date(2026, 1, 29),
            entry_time=now,
            exit_time=now,
            instrument="NIFTY",
            option_type="BUY",
            quantity=65,
            entry_price=Decimal("25195"),
            exit_price=Decimal("25195"),
            signal_percent=Decimal("70"),
            gross_pnl=Decimal("0"),
            total_charges=Decimal("4518.16"),
            net_pnl=Decimal("-4518.16"),
            exit_reason="STRATEGY_INVALIDATED",
        )
    )
    db.commit()
    assert repair_index_proxy_charges(db) == 2
    db.refresh(run)
    assert float(run.net_pnl) == 1576.25
    assert float(run.total_charges) == 0
    assert run.wins == 1
    assert "NFO" in (run.charge_note or "") or "proxy" in (run.charge_note or "").lower()
