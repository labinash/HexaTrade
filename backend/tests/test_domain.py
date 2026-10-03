from datetime import datetime, timedelta, timezone

from app.core.clock import IST
from app.domain.backtest import run_backtest
from app.domain.candles import Bar
from app.domain.exits import ExitPolicyParams
from app.domain.chain import atm_price, atm_strike
from app.domain.indicators import atr, ema, rsi
from app.domain.paper import simulate_fill
from app.domain.risk import RiskContext, evaluate_risk, orders_allowed
from app.domain.signals import classify_signal
from app.domain.strategy import MarketSnapshot, StrategyParams, evaluate_snapshot, snapshot_from_bars


def test_ema_seed_and_step():
    values = [1, 2, 3, 4, 5]
    result = ema(values, 3)
    assert result[0] is None and result[1] is None
    assert result[2] == 2
    assert result[3] == 3
    assert result[4] == 4


def test_rsi_bounds():
    rising = [float(i) for i in range(1, 30)]
    series = rsi(rising, 14)
    assert series[14] == 100


def test_atr_positive():
    highs = [10, 12, 11, 13, 14, 13]
    lows = [8, 9, 9, 10, 11, 10]
    closes = [9, 11, 10, 12, 13, 12]
    series = atr(highs, lows, closes, 3)
    assert series[2] is not None
    assert series[-1] > 0


def test_all_conditions_pass():
    snap = MarketSnapshot(
        close=100, open=99, ema=90, reference_close=90, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=95, rsi=55, atr=20, now=__import__("datetime").time(10, 30),
    )
    evaluation = evaluate_snapshot(snap, StrategyParams(), 15)
    assert evaluation.passed
    assert evaluation.direction == "BULLISH"
    assert evaluation.failed_names == []


def test_failed_conditions_are_named():
    snap = MarketSnapshot(
        close=100, open=101, ema=110, reference_close=99, oi=100, reference_oi=100,
        volume=10, volume_ema=50, vwap=120, rsi=80, atr=5, now=__import__("datetime").time(16, 0),
    )
    evaluation = evaluate_snapshot(snap, StrategyParams(), 15)
    assert not evaluation.passed
    assert "RSI" in evaluation.failed_names
    assert "VWAP" in evaluation.failed_names
    assert evaluation.direction == "NONE"


def test_sensex_without_atr_threshold_fails():
    snap = MarketSnapshot(
        close=100, open=99, ema=90, reference_close=90, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=95, rsi=55, atr=20, now=__import__("datetime").time(10, 30),
    )
    evaluation = evaluate_snapshot(snap, StrategyParams(), None)
    assert "ATR" in evaluation.failed_names


def _momentum_snap(close: float, reference: float) -> MarketSnapshot:
    return MarketSnapshot(
        close=close, open=close - 1, ema=close - 10, reference_close=reference, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=close - 5, rsi=55, atr=20, now=__import__("datetime").time(10, 30),
    )


def _momentum(evaluation) -> object:
    return next(item for item in evaluation.conditions if item.name == "Momentum")


def test_momentum_in_points_compares_index_points():
    params = StrategyParams(momentum_unit="points", momentum_min_points=5)
    assert _momentum(evaluate_snapshot(_momentum_snap(24506, 24500), params, 15)).status == "PASS"
    assert _momentum(evaluate_snapshot(_momentum_snap(24504, 24500), params, 15)).status == "FAIL"


def test_momentum_in_percent_compares_a_percentage_of_the_reference():
    params = StrategyParams(momentum_unit="percent", momentum_min_percent=0.05)
    # 0.05% of 24500 is about 12.25 points.
    passing = evaluate_snapshot(_momentum_snap(24515, 24500), params, 15)
    failing = evaluate_snapshot(_momentum_snap(24508, 24500), params, 15)
    assert _momentum(passing).status == "PASS"
    assert _momentum(failing).status == "FAIL"
    assert "0.05% from 09:20" in _momentum(passing).required
    assert "12.2 pts" in _momentum(passing).required


def test_momentum_reports_both_points_and_percent():
    evaluation = evaluate_snapshot(_momentum_snap(24550, 24500), StrategyParams(), 15)
    condition = _momentum(evaluation)
    assert "+50.00 pts" in condition.current
    assert "+0.204%" in condition.current
    assert evaluation.momentum == 50
    assert round(evaluation.momentum_percent, 4) == 0.2041


def test_momentum_percent_is_none_without_a_reference():
    snap = MarketSnapshot(
        close=24500, open=24499, ema=24400, reference_close=None, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=24450, rsi=55, atr=20, now=__import__("datetime").time(10, 30),
    )
    evaluation = evaluate_snapshot(snap, StrategyParams(momentum_unit="percent"), 15)
    assert evaluation.momentum_percent is None
    assert _momentum(evaluation).current == "missing"
    assert _momentum(evaluation).status == "FAIL"


def test_new_version_exposes_its_child_rows_without_a_lazy_load():
    """clone_version copies from the previous version's children, so a freshly
    built version must expose them while the session has autoflush off."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.bootstrap import _add_version
    from app.db.models import Strategy

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    strategy = Strategy(name="test")
    db.add(strategy)
    db.flush()
    version = _add_version(db, strategy, 1, "first")
    assert version.exit_policy is not None
    assert version.risk_policy is not None
    assert version.option_rule is not None
    assert [row.underlying for row in version.parameters] == ["NIFTY", "SENSEX"]
    assert all(row.momentum_unit == "points" for row in version.parameters)
    assert all(row.direction == "BULLISH" for row in version.parameters)


def test_live_requires_every_enabled_condition():
    snap = MarketSnapshot(
        close=100, open=99, ema=90, reference_close=90, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=95, rsi=80, atr=20, now=__import__("datetime").time(10, 30),
    )
    params = StrategyParams(confirmation_mode="min_n", min_conditions=1)
    assert evaluate_snapshot(snap, params, 15, live=False).passed
    assert not evaluate_snapshot(snap, params, 15, live=True).passed


def test_duplicate_signal_does_not_reenter():
    assert classify_signal(False, True).action == "NEW"
    assert classify_signal(True, True).action == "DUPLICATE"
    assert classify_signal(True, False).action == "NONE"


def test_analysis_mode_does_not_place_orders():
    assert not orders_allowed("ANALYSIS")
    decision = evaluate_risk(_risk("ANALYSIS"))
    assert not decision.allowed


def test_risk_rejects_bad_lot_multiple():
    context = _risk("PAPER")
    context.quantity = 10
    context.lot_size = 65
    decision = evaluate_risk(context)
    assert not decision.allowed
    assert any("lot size" in reason for reason in decision.reasons)


def test_paper_fill_adds_slippage_and_never_places_live_orders():
    from pathlib import Path

    source = Path("app/domain/paper.py").read_text(encoding="utf-8")
    assert "place_order" not in source
    assert "kiteconnect" not in source
    fill = simulate_fill("BUY", 100, 50, 0.5, 20, 0, data_stale=False)
    assert fill is not None
    assert fill.price == 100.5
    assert simulate_fill("BUY", 100, 50, 0.5, 20, 0, data_stale=True) is None


def test_backtest_does_not_use_future_bars_for_the_signal():
    bars = _trend_bars()
    changed = list(bars)
    changed[-1] = Bar(changed[-1].start, 1, 1, 1, 1, 1, 1)
    first = snapshot_from_bars(bars[:40], StrategyParams())
    second = snapshot_from_bars(changed[:40], StrategyParams())
    assert first.close == second.close
    assert first.ema == second.ema
    result = run_backtest(bars, StrategyParams(), ExitPolicyParams(stop_enabled=False, target_enabled=False, reversal_ema=False, reversal_vwap=False, time_exit_enabled=False), atr_threshold=1, slippage_points=0, brokerage_per_order=0, statutory_bps=0, quantity=1)
    if result.trades:
        assert result.trades[0].entry_time > bars[0].start


def _trend_bars():
    start = datetime(2026, 9, 29, 9, 15, tzinfo=IST)
    bars = []
    for index in range(80):
        price = 100 + index * 0.4
        moment = start + timedelta(minutes=index)
        bars.append(Bar(moment.astimezone(timezone.utc), price - 0.2, price + 0.3, price - 0.3, price, 1000 + index * 50, 10000 + index * 200))
    return bars


def _risk(mode: str) -> RiskContext:
    clock = __import__("datetime").time
    return RiskContext(
        mode=mode, kill_switch=False, data_stale=False, token_valid=True,
        now=clock(10, 30), session_start=clock(9, 15), session_end=clock(15, 30),
        entry_start=clock(9, 30), entry_stop=clock(15, 0), square_off=clock(15, 15),
        mis_cutoff=clock(15, 20), daily_pnl=0, max_daily_loss=5000, trades_today=0,
        max_trades_per_day=3, open_positions=0, max_open_positions=2, exposure=0,
        max_exposure=200000, quantity=65, lot_size=65, max_quantity=5000,
        capital_required=1000, max_capital=100000, spread=0.5, max_spread=2,
        option_volume=100, min_option_volume=0, option_oi=100, min_option_oi=0,
        exchange="NFO", expected_exchange="NFO", product="MIS", underlying_trades=0,
        max_trades_per_underlying=2, cooldown_active=False, duplicate_signal=False,
        redis_ok=True, database_ok=True,
    )


def test_atm_defaults_to_spot_so_chain_and_scanner_agree():
    strikes = [22400.0, 22450.0, 22500.0]
    assert atm_price(22421.95, 22520.0, "spot") == 22421.95
    assert atm_price(22421.95, 22520.0, "futures") == 22520.0
    assert atm_strike(strikes, 22421.95, 22520.0, basis="spot") == 22400.0
    assert atm_strike(strikes, 22421.95, 22520.0, basis="futures") == 22500.0
    assert atm_strike(strikes, 22421.95) == 22400.0
