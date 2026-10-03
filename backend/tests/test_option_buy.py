"""Independent CE and PE option-buy evaluators. Both use 9 conditions."""

from dataclasses import replace
from datetime import datetime, time, timezone

from app.core.clock import IST
from app.domain.candles import Bar
from app.domain.exits import ExitContext, ExitPolicyParams, PositionState, advance_trail, choose_exit, trail_is_armed
from app.domain.option_buy import (
    CE_TOTAL,
    PE_TOTAL,
    FuturesState,
    OptionBuyConfig,
    OptionPremiumState,
    empty_result,
    evaluate_ce_buy_strategy,
    evaluate_pe_buy_strategy,
    observation_at,
    option_premium_state,
)


NOW = datetime(2026, 9, 30, 10, 45, tzinfo=IST)
CONFIG = OptionBuyConfig(underlying="NIFTY", atr_threshold=15)


def _premium(**fields) -> OptionPremiumState:
    base = OptionPremiumState(
        close=104,
        open=103,
        ema=100,
        oi=20000,
        prev_oi=18000,
        prev_oi_found=True,
        ref_0920_close=100,
        ref_0920_found=True,
        ref_1100_close=101,
        ref_1100_found=True,
        ref_1100_oi=5000,
        ref_1100_oi_found=True,
        option_type="CE",
        symbol="NIFTY25SEP25000CE",
        expiry="2026-09-30",
        strike=25000,
    )
    return replace(base, **fields) if fields else base


def _futures(**fields) -> FuturesState:
    base = FuturesState(close=25020, volume=3000, volume_ema=1500, vwap=24980, rsi=55, atr=22, symbol="NIFTY26SEPFUT")
    return replace(base, **fields) if fields else base


def _bar(hour: int, minute: int, close: float, day=None, oi: float = 0) -> Bar:
    start = datetime(2026, 9, day or 30, hour, minute, tzinfo=IST)
    return Bar(start, close - 1, close + 1, close - 1, close, 1000, oi)


def test_ce_and_pe_use_the_same_nine_conditions():
    ce = evaluate_ce_buy_strategy(_premium(), _futures(), CONFIG, NOW)
    pe = evaluate_pe_buy_strategy(_premium(option_type="PE"), _futures(close=24900), CONFIG, NOW)
    assert ce.total_count == CE_TOTAL == 9
    assert pe.total_count == PE_TOTAL == 9
    assert [row.name for row in ce.conditions] == [row.name for row in pe.conditions]
    assert "Premium Since 11:00" not in [row.name for row in pe.conditions]
    assert "OI Since 11:00" not in [row.name for row in pe.conditions]


def test_ce_buy_setup_requires_every_condition():
    result = evaluate_ce_buy_strategy(_premium(), _futures(), CONFIG, NOW)
    assert result.status == "BUY SETUP"
    assert result.passed
    assert result.passed_count == 9


def test_pe_buy_setup_requires_every_condition():
    result = evaluate_pe_buy_strategy(
        _premium(option_type="PE"),
        _futures(close=24900, rsi=45),
        CONFIG,
        NOW,
    )
    assert result.status == "BUY SETUP"
    assert result.passed_count == 9


def test_ce_failure_does_not_force_pe_to_fail():
    weak_ce = _premium(close=90, ema=100, open=89)
    strong_pe = _premium(option_type="PE", close=104, ema=100, open=103)
    futures = _futures(close=24900, rsi=45)
    ce = evaluate_ce_buy_strategy(weak_ce, futures, CONFIG, NOW)
    pe = evaluate_pe_buy_strategy(strong_pe, futures, CONFIG, NOW)
    assert ce.status == "NO TRADE"
    assert pe.status == "BUY SETUP"


def test_pe_failure_does_not_force_ce_to_fail():
    futures = _futures(close=25020, vwap=24980, rsi=55)
    ce = evaluate_ce_buy_strategy(_premium(), futures, CONFIG, NOW)
    pe = evaluate_pe_buy_strategy(_premium(option_type="PE"), futures, CONFIG, NOW)
    assert ce.status == "BUY SETUP"
    assert pe.status == "NO TRADE"
    vwap = next(row for row in pe.conditions if row.name == "Futures VWAP")
    assert vwap.status == "FAIL"
    assert "below" in vwap.required or "<" in vwap.required


def test_missing_0920_blocks_setup_and_does_not_use_current():
    option = _premium(ref_0920_close=None, ref_0920_found=False, close=200)
    result = evaluate_ce_buy_strategy(option, _futures(), CONFIG, NOW)
    row = next(item for item in result.conditions if item.name == "Premium Momentum")
    assert row.status == "DATA UNAVAILABLE"
    assert result.status == "NO TRADE"
    assert "09:20" in row.failure_reason


def test_zero_reference_premium_is_not_a_divisor():
    option = _premium(ref_0920_close=0, ref_0920_found=True)
    result = evaluate_ce_buy_strategy(option, _futures(), CONFIG, NOW)
    row = next(item for item in result.conditions if item.name == "Premium Momentum")
    assert row.status == "DATA UNAVAILABLE"
    assert "zero" in row.failure_reason.lower()


def test_missing_1100_does_not_block_pe():
    option = _premium(option_type="PE", ref_1100_oi=None, ref_1100_oi_found=False, ref_1100_close=None, ref_1100_found=False)
    pe = evaluate_pe_buy_strategy(option, _futures(close=24900, rsi=45), CONFIG, NOW)
    ce = evaluate_ce_buy_strategy(_premium(), _futures(), CONFIG, NOW)
    assert pe.status == "BUY SETUP"
    assert ce.status == "BUY SETUP"
    assert all(row.name != "OI Since 11:00" for row in pe.conditions)


def test_observation_at_ignores_other_dates_and_nearby_minutes():
    today = [_bar(9, 21, 110), _bar(10, 0, 120)]
    yesterday = [_bar(9, 20, 50, day=29)]
    found = observation_at(yesterday + today, time(9, 20), session_date=__import__("datetime").date(2026, 9, 30))
    assert found is None
    exact = observation_at([_bar(9, 20, 99), _bar(10, 0, 120)], time(9, 20), session_date=__import__("datetime").date(2026, 9, 30))
    assert exact is not None and exact.close == 99


def test_yesterday_0920_is_not_used_for_today():
    bars = [_bar(9, 20, 80, day=29), _bar(10, 0, 104)]
    state = option_premium_state(bars, bars, CONFIG, session_date=__import__("datetime").date(2026, 9, 30))
    assert not state.ref_0920_found


def test_sensex_without_atr_threshold_is_unavailable():
    config = OptionBuyConfig(underlying="SENSEX", atr_threshold=None)
    result = evaluate_ce_buy_strategy(_premium(), _futures(), config, NOW)
    row = next(item for item in result.conditions if item.name == "Futures ATR")
    assert row.status == "DATA UNAVAILABLE"
    assert "SENSEX" in row.failure_reason
    assert result.status == "NO TRADE"


def test_ce_rsi_band_is_not_the_pe_band():
    futures = _futures(rsi=35)
    ce = evaluate_ce_buy_strategy(_premium(), futures, CONFIG, NOW)
    pe = evaluate_pe_buy_strategy(_premium(option_type="PE"), _futures(close=24900, rsi=35), CONFIG, NOW)
    assert next(row for row in ce.conditions if row.name == "Futures RSI").status == "FAIL"
    assert next(row for row in pe.conditions if row.name == "Futures RSI").status == "PASS"


def test_empty_result_keeps_the_strategy_denominator():
    ce = empty_result("CE", CONFIG, "no contract", NOW)
    pe = empty_result("PE", CONFIG, "no contract", NOW)
    assert ce.total_count == 9 and ce.status == "NO TRADE"
    assert pe.total_count == 9 and pe.status == "NO TRADE"


def test_trail_stays_off_until_half_the_target_distance():
    policy = ExitPolicyParams(trail_enabled=True, trail_arm_fraction=0.5, trail_style="points", trail_points=2)
    state = PositionState(
        entry_price=100,
        entry_time=datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc),
        stop_loss=70,
        target=160,
        trail_price=None,
        atr_at_entry=None,
    )
    assert not trail_is_armed(state, 129, policy)
    assert trail_is_armed(state, 130, policy)
    assert advance_trail(state, 129, policy, None) is None
    assert advance_trail(state, 130, policy, None) == 128


def test_premium_trend_exit_uses_persisted_entry_not_a_fresh_signal():
    policy = ExitPolicyParams(
        reversal_ema=False, reversal_vwap=False, reversal_rsi=False,
        stop_enabled=False, target_enabled=False, trail_enabled=False, time_exit_enabled=False,
        premium_trend_exit=True,
    )
    state = PositionState(
        entry_price=100,
        entry_time=datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc),
        stop_loss=70,
        target=160,
        trail_price=None,
        atr_at_entry=None,
    )
    reason = choose_exit(
        state,
        ExitContext(
            premium=90,
            now=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc),
            clock=time(11, 0),
            close=25000,
            ema=24900,
            vwap=24900,
            rsi=55,
            rsi_min=40,
            live_atr=20,
            kill_switch=False,
            daily_loss_hit=False,
            data_stale=False,
            premium_ema=100,
        ),
        policy,
    )
    assert reason == "STRATEGY_INVALIDATED"
    still_open = choose_exit(
        state,
        ExitContext(
            premium=110,
            now=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc),
            clock=time(11, 0),
            close=25000,
            ema=24900,
            vwap=24900,
            rsi=55,
            rsi_min=40,
            live_atr=20,
            kill_switch=False,
            daily_loss_hit=False,
            data_stale=False,
            premium_ema=100,
        ),
        policy,
    )
    assert still_open is None


def test_premium_below_ema_does_not_cut_a_winner():
    policy = ExitPolicyParams(
        reversal_ema=False, reversal_vwap=False, reversal_rsi=False,
        stop_enabled=False, target_enabled=False, trail_enabled=False, time_exit_enabled=False,
        premium_trend_exit=True,
    )
    state = PositionState(
        entry_price=100,
        entry_time=datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc),
        stop_loss=70,
        target=160,
        trail_price=None,
        atr_at_entry=None,
    )
    still_open = choose_exit(
        state,
        ExitContext(
            premium=105,
            now=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc),
            clock=time(11, 0),
            close=25000,
            ema=24900,
            vwap=24900,
            rsi=55,
            rsi_min=40,
            live_atr=20,
            kill_switch=False,
            daily_loss_hit=False,
            data_stale=False,
            premium_ema=110,
        ),
        policy,
    )
    assert still_open is None
