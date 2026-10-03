"""Directional and neutral condition behaviour.

The directional conditions mirror when the configured direction is BEARISH. The
neutral ones must read identically in both directions, which is what most of the
paired assertions below are protecting.
"""

from dataclasses import replace
from datetime import datetime, time, timezone

from app.domain.exits import ExitContext, ExitPolicyParams, PositionState, choose_exit
from app.domain.strategy import (
    MarketSnapshot,
    StrategyParams,
    evaluate_atr,
    evaluate_candle,
    evaluate_ema,
    evaluate_momentum,
    evaluate_oi,
    evaluate_rsi,
    evaluate_snapshot,
    evaluate_time,
    evaluate_volume,
    evaluate_vwap,
)

BULL = StrategyParams(direction="BULLISH")
BEAR = StrategyParams(direction="BEARISH")


def test_ema_mirrors_with_direction():
    assert evaluate_ema(BULL, close=100, ema=90).status == "PASS"
    assert evaluate_ema(BULL, close=90, ema=100).status == "FAIL"
    assert evaluate_ema(BEAR, close=90, ema=100).status == "PASS"
    assert evaluate_ema(BEAR, close=100, ema=90).status == "FAIL"


def test_momentum_bearish_requires_a_negative_move():
    bull = StrategyParams(direction="BULLISH", momentum_min_points=5)
    bear = StrategyParams(direction="BEARISH", momentum_min_points=5)
    assert evaluate_momentum(bull, 6, None, None).status == "PASS"
    assert evaluate_momentum(bull, 4, None, None).status == "FAIL"
    assert evaluate_momentum(bear, -6, None, None).status == "PASS"
    assert evaluate_momentum(bear, -4, None, None).status == "FAIL"


def test_momentum_bearish_rejects_an_upward_move_below_the_threshold():
    """A +4 move is not bearish momentum even though it is under +5."""
    bear = StrategyParams(direction="BEARISH", momentum_min_points=5)
    condition = evaluate_momentum(bear, 4, None, None)
    assert condition.status == "FAIL"
    assert condition.required.startswith("< -5")


def test_momentum_percent_mirrors_too():
    bear = StrategyParams(direction="BEARISH", momentum_unit="percent", momentum_min_percent=0.05)
    assert evaluate_momentum(bear, -30, -0.1, 30000).status == "PASS"
    assert evaluate_momentum(bear, -6, -0.02, 30000).status == "FAIL"
    assert evaluate_momentum(bear, 30, 0.1, 30000).status == "FAIL"


def test_vwap_mirrors_with_direction():
    assert evaluate_vwap(BULL, close=100, vwap=95).status == "PASS"
    assert evaluate_vwap(BULL, close=95, vwap=100).status == "FAIL"
    assert evaluate_vwap(BEAR, close=95, vwap=100).status == "PASS"
    assert evaluate_vwap(BEAR, close=100, vwap=95).status == "FAIL"


def test_candle_mirrors_and_a_doji_fails_both_ways():
    assert evaluate_candle(BULL, open_price=99, close=100).status == "PASS"
    assert evaluate_candle(BULL, open_price=100, close=99).status == "FAIL"
    assert evaluate_candle(BEAR, open_price=100, close=99).status == "PASS"
    assert evaluate_candle(BEAR, open_price=99, close=100).status == "FAIL"
    assert evaluate_candle(BULL, open_price=100, close=100).status == "FAIL"
    assert evaluate_candle(BEAR, open_price=100, close=100).status == "FAIL"


def test_rsi_uses_a_separate_band_per_direction():
    bull = StrategyParams(direction="BULLISH", rsi_min=40, rsi_max=70)
    bear = StrategyParams(direction="BEARISH", rsi_min_bearish=30, rsi_max_bearish=60)
    assert evaluate_rsi(bull, 50).status == "PASS"
    assert evaluate_rsi(bull, 35).status == "FAIL"
    assert evaluate_rsi(bear, 50).status == "PASS"
    assert evaluate_rsi(bear, 65).status == "FAIL"
    assert evaluate_rsi(bear, 35).status == "PASS"


def test_rsi_band_is_inclusive_at_both_edges():
    bull = StrategyParams(direction="BULLISH", rsi_min=40, rsi_max=70)
    assert evaluate_rsi(bull, 40).status == "PASS"
    assert evaluate_rsi(bull, 70).status == "PASS"


def test_rsi_bearish_band_is_configurable_not_fixed_at_30_60():
    bear = StrategyParams(direction="BEARISH", rsi_min_bearish=25, rsi_max_bearish=55)
    assert evaluate_rsi(bear, 58).status == "FAIL"
    assert evaluate_rsi(bear, 27).status == "PASS"


def test_volume_is_direction_neutral():
    for params in (BULL, BEAR):
        assert evaluate_volume(params, volume=2000, volume_ema=1000).status == "PASS"
        assert evaluate_volume(params, volume=500, volume_ema=1000).status == "FAIL"
        assert evaluate_volume(params, volume=2000, volume_ema=1000).is_directional is False


def test_oi_is_direction_neutral():
    bull = StrategyParams(direction="BULLISH", oi_min_change=10000)
    bear = StrategyParams(direction="BEARISH", oi_min_change=10000)
    for params in (bull, bear):
        assert evaluate_oi(params, 15000).status == "PASS"
        assert evaluate_oi(params, -15000).status == "FAIL"
        assert evaluate_oi(params, 15000).is_directional is False


def test_atr_is_direction_neutral():
    assert evaluate_atr(20, 15).status == "PASS"
    assert evaluate_atr(10, 15).status == "FAIL"
    assert evaluate_atr(20, 15).is_directional is False


def test_time_is_direction_neutral():
    inside = time(10, 30)
    outside = time(16, 0)
    for params in (BULL, BEAR):
        assert evaluate_time(params, inside).status == "PASS"
        assert evaluate_time(params, outside).status == "FAIL"
        assert evaluate_time(params, inside).is_directional is False


def _bearish_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        close=90, open=95, ema=100, reference_close=100, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=95, rsi=45, atr=20, now=time(10, 30),
    )


def test_bearish_snapshot_produces_a_bearish_signal():
    evaluation = evaluate_snapshot(_bearish_snapshot(), BEAR, 15)
    assert evaluation.passed
    assert evaluation.direction == "BEARISH"
    assert evaluation.overall_signal() == "BEARISH_SIGNAL"
    assert evaluation.failed_names == []
    assert evaluation.passed_conditions == evaluation.total_conditions


def test_the_same_snapshot_is_no_trade_when_configured_bullish():
    evaluation = evaluate_snapshot(_bearish_snapshot(), BULL, 15)
    assert not evaluation.passed
    assert evaluation.direction == "NONE"
    assert evaluation.overall_signal() == "NO_TRADE"
    assert {"EMA Trend", "Momentum", "VWAP", "Candle"} <= set(evaluation.failed_names)


def test_a_partial_pass_never_becomes_a_signal():
    snap = replace(_bearish_snapshot(), volume=10)  # only the neutral volume test fails
    evaluation = evaluate_snapshot(snap, BEAR, 15)
    assert not evaluation.passed
    assert evaluation.overall_signal() == "NO_TRADE"
    assert evaluation.failed_names == ["Futures Volume"]


def test_failed_conditions_carry_a_reason_and_their_direction():
    evaluation = evaluate_snapshot(_bearish_snapshot(), BULL, 15)
    by_name = {item.name: item for item in evaluation.conditions}
    assert by_name["EMA Trend"].failure_reason
    assert by_name["EMA Trend"].direction == "BULLISH"
    assert by_name["OI Change"].direction == "NEUTRAL"
    assert by_name["Futures Volume"].failure_reason == ""


def test_required_text_changes_with_direction():
    bullish = {c.name: c.required for c in evaluate_snapshot(_bearish_snapshot(), BULL, 15).conditions}
    bearish = {c.name: c.required for c in evaluate_snapshot(_bearish_snapshot(), BEAR, 15).conditions}
    assert bullish["EMA Trend"].startswith("Close > EMA20")
    assert bearish["EMA Trend"].startswith("Close < EMA20")
    assert bullish["VWAP"] == "Close > VWAP"
    assert bearish["VWAP"] == "Close < VWAP"
    # Neutral conditions read the same either way.
    assert bullish["OI Change"] == bearish["OI Change"]
    assert bullish["Futures Volume"] == bearish["Futures Volume"]
    assert bullish["Time"] == bearish["Time"]


def test_direction_maps_to_the_option_type():
    assert BULL.option_type() == "CE"
    assert BEAR.option_type() == "PE"


def _exit_context(direction: str) -> ExitContext:
    """Underlying below its EMA and VWAP with a weak RSI. That is a healthy
    bearish trade and a broken bullish one."""
    return ExitContext(
        premium=100, now=datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc), clock=time(10, 30),
        close=90, ema=100, vwap=95, rsi=35, rsi_min=40, rsi_max=60,
        live_atr=20, kill_switch=False, daily_loss_hit=False, data_stale=False,
        direction=direction,
    )


def test_reversal_exits_mirror_for_a_bearish_position():
    policy = ExitPolicyParams(
        stop_enabled=False, target_enabled=False, trail_enabled=False,
        reversal_ema=True, reversal_vwap=True, reversal_rsi=True,
        time_exit_enabled=False, max_hold_enabled=False,
        priority=("STRATEGY_INVALIDATED",),
    )
    state = PositionState(
        entry_price=100, entry_time=datetime(2026, 9, 29, 4, 30, tzinfo=timezone.utc),
        stop_loss=None, target=None, trail_price=None, atr_at_entry=20,
    )
    assert choose_exit(state, _exit_context("BULLISH"), policy) == "STRATEGY_INVALIDATED"
    assert choose_exit(state, _exit_context("BEARISH"), policy) is None


def test_pe_is_not_invalidated_while_futures_remain_below_vwap():
    policy = ExitPolicyParams(
        stop_enabled=False, target_enabled=False, trail_enabled=False,
        reversal_ema=True, reversal_vwap=True, reversal_rsi=False,
        time_exit_enabled=False, max_hold_enabled=False, premium_trend_exit=False,
        priority=("STRATEGY_INVALIDATED",),
    )
    state = PositionState(
        entry_price=129.1, entry_time=datetime(2026, 10, 1, 3, 20, tzinfo=timezone.utc),
        stop_loss=None, target=None, trail_price=None, atr_at_entry=None,
    )
    ctx = ExitContext(
        premium=138, now=datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc), clock=time(9, 0),
        close=22443, ema=22438, vwap=22547, rsi=45, rsi_min=40, rsi_max=60,
        live_atr=20, kill_switch=False, daily_loss_hit=False, data_stale=False,
        direction="BEARISH", premium_ema=130,
    )
    assert choose_exit(state, ctx, policy) is None


def test_pe_invalidates_only_after_vwap_is_reclaimed():
    policy = ExitPolicyParams(
        stop_enabled=False, target_enabled=False, trail_enabled=False,
        reversal_ema=True, reversal_vwap=True, reversal_rsi=False,
        time_exit_enabled=False, max_hold_enabled=False, premium_trend_exit=False,
        priority=("STRATEGY_INVALIDATED",),
    )
    state = PositionState(
        entry_price=129.1, entry_time=datetime(2026, 10, 1, 3, 20, tzinfo=timezone.utc),
        stop_loss=None, target=None, trail_price=None, atr_at_entry=None,
    )
    ctx = ExitContext(
        premium=120, now=datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc), clock=time(9, 0),
        close=22560, ema=22438, vwap=22547, rsi=55, rsi_min=40, rsi_max=60,
        live_atr=20, kill_switch=False, daily_loss_hit=False, data_stale=False,
        direction="BEARISH",
    )
    assert choose_exit(state, ctx, policy) == "STRATEGY_INVALIDATED"
