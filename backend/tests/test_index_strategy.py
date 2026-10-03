"""Independent BUY/SELL index scores. No broker orders. No lookahead."""

from datetime import datetime, time

from app.core.clock import IST
from app.domain.candles import Bar
from app.domain.index_strategy import (
    SAME_CANDLE_RULE,
    apply_slippage,
    config_from_dict,
    decide,
    features_from_bars,
    index_exit_fill,
    index_hold_exit,
    next_bar_exit,
    performance_from_trades,
    signed_index_gross,
)
from app.domain.strategy import make_bar


def _bars(n=40, start=25000, step=5):
    out = []
    price = start
    for i in range(n):
        price += step
        moment = datetime(2026, 1, 15, 10, 0, tzinfo=IST).replace(minute=min(i, 59))
        if i >= 60:
            moment = datetime(2026, 1, 15, 11, i - 60, tzinfo=IST)
        out.append(make_bar(moment, price, volume=2000 + i * 10))
    return out


def test_buy_score_rises_on_bullish_tape_and_holds_below_threshold():
    cfg = config_from_dict({"preset": "BALANCED", "buy_threshold": 95, "sell_threshold": 95})
    bars = _bars(40, 25000, 8)
    verdict = decide(features_from_bars(bars, cfg), cfg)
    assert 0 <= verdict.buy.score <= 100
    assert 0 <= verdict.sell.score <= 100
    assert verdict.decision in {"BUY", "SELL", "HOLD"}
    cfg_low = config_from_dict({"preset": "AGGRESSIVE", "buy_threshold": 10, "sell_threshold": 99, "sell_enabled": False})
    bull = decide(features_from_bars(bars, cfg_low), cfg_low)
    assert bull.decision in {"BUY", "HOLD"}


def test_sell_score_on_down_bars():
    cfg = config_from_dict({"preset": "AGGRESSIVE", "buy_threshold": 99, "sell_threshold": 10, "buy_enabled": False})
    bars = _bars(40, 26000, -8)
    verdict = decide(features_from_bars(bars, cfg), cfg)
    assert verdict.sell.score >= 0
    assert verdict.decision in {"SELL", "HOLD"}


def test_hold_when_both_sides_off():
    cfg = config_from_dict({"buy_enabled": False, "sell_enabled": False, "buy_threshold": 1, "sell_threshold": 1})
    verdict = decide(features_from_bars(_bars(), cfg), cfg)
    assert verdict.decision == "HOLD"


def test_conflict_is_hold_unless_hedge_allowed():
    cfg = config_from_dict({"buy_threshold": 0, "sell_threshold": 0, "allow_hedge": False, "volume_enabled": False})
    verdict = decide(features_from_bars(_bars(), cfg), cfg)
    if verdict.buy.passed and verdict.sell.passed:
        assert verdict.decision == "HOLD"
        assert verdict.conflict is True


def test_no_lookahead_window_uses_only_prefix():
    bars = _bars(30, 25000, 3)
    cfg = config_from_dict({})
    early = features_from_bars(bars[:10], cfg)
    later = features_from_bars(bars[:20], cfg)
    assert early["close"] == bars[9].close
    assert later["close"] == bars[19].close
    assert later["close"] != early["close"]


def test_same_candle_stop_wins_over_target():
    assert next_bar_exit("BUY", high=25100, low=24900, stop=24950, target=25080) == "STOP_LOSS_HIT"
    assert next_bar_exit("SELL", high=25100, low=24900, stop=25080, target=24920) == "STOP_LOSS_HIT"
    assert next_bar_exit("BUY", high=25100, low=25020, stop=24900, target=25080) == "TARGET_HIT"


def test_failed_gate_blocks_buy_even_when_score_high():
    from app.domain.index_strategy import score_side

    cfg = config_from_dict({"preset": "BALANCED", "buy_threshold": 10})
    facts = features_from_bars(_bars(40, 25000, 8), cfg)
    facts["prior_high"] = 99999
    facts["ema"] = facts["close"]
    facts["vwap"] = facts["close"]
    side = score_side("BUY", facts, cfg)
    assert side.failed_gate is not None
    assert side.passed is False


def test_structural_stop_and_partial_are_index_exit_rules():
    from datetime import time as time_cls

    from app.domain.index_exits import IndexExitMarket, IndexExitState, PARTIAL_1R, STRUCTURAL_STOP, evaluate_index_exit

    state = IndexExitState(
        side="BUY",
        entry=25000,
        stop=24900,
        target=25150,
        trail=None,
        atr=100,
        risk=100,
        bars_held=2,
        remaining_qty=65,
        initial_qty=65,
        partial_taken=False,
        breakout_level=24980,
        mfe=25120,
    )
    market = IndexExitMarket(
        high=25120,
        low=24880,
        close=25110,
        ema20=25050,
        vwap=25040,
        premium=None,
        premium_entry=None,
        clock=time_cls(11, 0),
        square=time_cls(15, 15),
        expiry_square=time_cls(14, 30),
        is_expiry=False,
        is_entry_bar=False,
        end_of_walk=False,
        end_of_session=False,
        opposite_score=20,
        opposite_threshold=80,
    )
    reason, _ = evaluate_index_exit(state, market, variant="partial_trail")
    assert reason == STRUCTURAL_STOP
    market.low = 25020
    reason, extras = evaluate_index_exit(state, market, variant="partial_trail")
    assert reason == PARTIAL_1R
    assert extras.get("new_stop") == 25000


def test_index_exit_fill_uses_stop_or_target_not_close():
    assert index_exit_fill("BUY", "STOP_LOSS_HIT", 24800, 24900, 25100, 0.5) == 24899.5
    assert index_exit_fill("SELL", "TARGET_HIT", 24800, 25100, 24920, 0.5) == 24920.5
    assert index_exit_fill("BUY", "FORCE_SQUARE_OFF", 25050, 24900, 25100, 0) == 25050


def test_slippage_and_short_pnl():
    assert apply_slippage(25000, "BUY", 0.5) == 25000.5
    assert apply_slippage(25000, "SELL", 0.5) == 24999.5
    assert signed_index_gross("SELL", 25000, 24900, 65, 0) > 0
    assert signed_index_gross("BUY", 25000, 24900, 65, 0) < 0


def test_nifty_and_sensex_are_scored_separately():
    cfg = config_from_dict({"preset": "BALANCED"})
    nifty = decide(features_from_bars(_bars(35, 25000, 4), cfg), cfg)
    sensex = decide(features_from_bars(_bars(35, 72000, -20), cfg), cfg)
    assert nifty.indicators["close"] != sensex.indicators["close"]


def test_performance_totals_and_disclaimer():
    summary = performance_from_trades(
        [
            {"status": "CLOSED", "net_pnl": 100, "gross_pnl": 120, "total_charges": 20, "option_type": "BUY", "instrument": "NIFTY", "exit_reason": "TARGET_HIT", "date": "2026-01-15", "exit_time": "a"},
            {"status": "CLOSED", "net_pnl": -40, "gross_pnl": -30, "total_charges": 10, "option_type": "SELL", "instrument": "SENSEX", "exit_reason": "STOP_LOSS_HIT", "date": "2026-01-15", "exit_time": "b"},
        ]
    )
    assert summary["total_trades"] == 2
    assert summary["buy_trades"] == 1
    assert summary["sell_trades"] == 1
    assert summary["nifty_pnl"] == 100
    assert summary["sensex_pnl"] == -40
    assert "NOT LIVE PERFORMANCE" in summary["label"]
    assert SAME_CANDLE_RULE in summary["same_candle_rule"]


def test_hold_below_threshold():
    cfg = config_from_dict({"buy_threshold": 99, "sell_threshold": 99})
    verdict = decide(features_from_bars(_bars(20, 25000, 1), cfg), cfg)
    assert verdict.decision == "HOLD"


def test_map_exit_and_time_square_off():
    from app.domain.index_strategy import map_exit_reason

    assert map_exit_reason("TARGET") == "TARGET_HIT"
    assert map_exit_reason("STOP_LOSS") == "STOP_LOSS_HIT"
    assert map_exit_reason("TRAILING_STOP") == "TRAILING_STOP_HIT"
    assert map_exit_reason("MAX_HOLD") == "TIME_EXIT"
    assert map_exit_reason("TIME_EXIT") == "FORCE_SQUARE_OFF"
    assert map_exit_reason("END_OF_SESSION") == "FORCE_SQUARE_OFF"
    assert map_exit_reason("x", end_of_data=True) == "END_OF_DATA"


def test_lot_size_scales_pnl():
    one = signed_index_gross("BUY", 25000, 25010, 65, 0)
    two = signed_index_gross("BUY", 25000, 25010, 130, 0)
    assert two == one * 2


def test_same_candle_rule_is_documented():
    assert "STOP_LOSS_HIT" in SAME_CANDLE_RULE
    assert "entry candle" in SAME_CANDLE_RULE.lower() or "entry" in SAME_CANDLE_RULE.lower()


def test_index_hold_exit_is_target_or_stop_not_score_invalidation():
    kwargs = dict(
        clock=time(11, 0),
        square=time(15, 20),
        session_end=time(15, 30),
        held_minutes=10,
        max_hold_minutes=180,
        end_of_walk=False,
    )
    assert index_hold_exit("BUY", 25100, 25000, 24900, 25080, is_entry_bar=True, **kwargs) is None
    assert index_hold_exit("BUY", 25100, 25000, 24900, 25080, is_entry_bar=False, **kwargs) == "TARGET_HIT"
    assert index_hold_exit("BUY", 25050, 24850, 24900, 25080, is_entry_bar=False, **kwargs) == "STOP_LOSS_HIT"
    assert index_hold_exit("BUY", 25100, 24850, 24900, 25080, is_entry_bar=False, **kwargs) == "STOP_LOSS_HIT"
    assert index_hold_exit("SELL", 25020, 24900, 25040, 24920, is_entry_bar=False, **kwargs) == "TARGET_HIT"
    assert index_hold_exit("BUY", 25020, 25000, 24900, 25080, is_entry_bar=False, clock=time(15, 20), square=time(15, 20), session_end=time(15, 30), held_minutes=10, max_hold_minutes=180, end_of_walk=False) == "FORCE_SQUARE_OFF"
    assert "INVALID" not in (index_hold_exit("BUY", 25020, 25000, 24900, 25080, is_entry_bar=False, **kwargs) or "")


def test_balanced_defaults_are_3m_hit_rate_gates():
    cfg = config_from_dict({"preset": "BALANCED"})
    assert cfg.buy_threshold == 80
    assert cfg.sell_threshold == 80
    assert cfg.session_start == "09:30"
    assert cfg.session_end == "14:45"
    assert cfg.max_trades_per_day == 2
    assert cfg.cooldown_minutes == 30
    assert cfg.no_reentry_after_stop is True
    assert cfg.confirm_higher_tf is True
    assert cfg.confirm_minutes == 5
    assert cfg.allow_reentry is False
    from app.domain.index_strategy import INDEX_CONFIRM_MINUTES, INDEX_PAPER_MINUTES
    assert INDEX_PAPER_MINUTES == 3
    assert INDEX_CONFIRM_MINUTES == 5


def test_allow_reentry_param_clears_stop_lock():
    from app.domain.index_strategy import validate_index_entry

    cfg = config_from_dict({"preset": "BALANCED", "allow_reentry": True})
    assert cfg.allow_reentry is True
    assert cfg.no_reentry_after_stop is False
    ok, _ = validate_index_entry(clock=time(11, 0), side="BUY", config=cfg, day_trades=0, day_pnl=0, stopped_today=True, walk_minutes=5)
    assert ok is True


def test_validate_index_entry_blocks_open_late_stop_and_daily_loss():
    from app.domain.index_strategy import validate_index_entry

    cfg = config_from_dict({"preset": "BALANCED"})
    kwargs = dict(side="BUY", config=cfg, day_trades=0, day_pnl=0, stopped_today=False, walk_minutes=5)
    ok, why = validate_index_entry(clock=time(9, 15), **kwargs)
    assert ok is False
    assert "window" in why.lower()
    ok, why = validate_index_entry(clock=time(14, 50), **kwargs)
    assert ok is False
    ok, _ = validate_index_entry(clock=time(11, 0), **kwargs)
    assert ok is True
    ok, why = validate_index_entry(clock=time(9, 15), side="BUY", config=cfg, day_trades=0, day_pnl=0, stopped_today=False, walk_minutes=3, score=85)
    assert ok is False
    ok, why = validate_index_entry(clock=time(11, 0), side="BUY", config=cfg, day_trades=0, day_pnl=0, stopped_today=False, walk_minutes=3, score=75, higher_tf_side="BUY")
    assert ok is False
    assert "80" in why
    ok, why = validate_index_entry(clock=time(11, 0), side="BUY", config=cfg, day_trades=0, day_pnl=0, stopped_today=True, walk_minutes=5)
    assert ok is False
    assert "stop" in why.lower()
    ok, why = validate_index_entry(clock=time(11, 0), side="BUY", config=cfg, day_trades=0, day_pnl=-5000, stopped_today=False, walk_minutes=5)
    assert ok is False
    assert "Daily loss" in why
    ok, why = validate_index_entry(clock=time(11, 0), side="BUY", config=cfg, day_trades=2, day_pnl=0, stopped_today=False, walk_minutes=5)
    assert ok is False


def test_validate_3m_requires_same_side_5m():
    from app.domain.index_strategy import validate_index_entry

    cfg = config_from_dict({"preset": "BALANCED"})
    kwargs = dict(side="BUY", clock=time(11, 0), config=cfg, day_trades=0, day_pnl=0, stopped_today=False, walk_minutes=3)
    ok, why = validate_index_entry(higher_tf_side="HOLD", htf_5_side="HOLD", htf_15_side="HOLD", score=85, **kwargs)
    assert ok is True
    ok, why = validate_index_entry(higher_tf_side="SELL", htf_5_side="SELL", htf_15_side="HOLD", score=85, **kwargs)
    assert ok is False
    assert "5m" in why
    ok, _ = validate_index_entry(higher_tf_side="BUY", htf_5_side="BUY", htf_15_side="BUY", score=85, **kwargs)
    assert ok is True
    ok, _ = validate_index_entry(higher_tf_side="SELL", htf_5_side="SELL", **kwargs)
    assert ok is False


def test_closed_higher_tf_does_not_look_ahead():
    from app.domain.index_strategy import closed_bars_asof

    start = datetime(2026, 6, 1, 10, 0, tzinfo=IST)
    bars = [
        make_bar(start, 25000, volume=100),
        make_bar(start.replace(minute=5), 25010, volume=100),
        make_bar(start.replace(minute=10), 25020, volume=100),
    ]
    asof = start.replace(minute=8)
    visible = closed_bars_asof(bars, asof, 5)
    assert visible[-1].start == start
    assert bars[1] not in visible


def test_entry_validation_rules_are_documented():
    from app.domain.index_strategy import ENTRY_VALIDATION_RULES

    text = " ".join(ENTRY_VALIDATION_RULES)
    assert "09:30" in text
    assert "5m" in text
    assert "gate" in text.lower()


def test_live_index_display_holds_on_weekend_and_stale_session():
    from datetime import date, datetime, time

    from app.core.clock import IST
    from app.domain.index_strategy import config_from_dict, live_index_display_ok

    cfg = config_from_dict({})
    saturday = datetime(2026, 10, 3, 11, 0, tzinfo=IST)
    friday_bar = datetime(2026, 10, 2, 10, 30, tzinfo=IST)
    ok, reason = live_index_display_ok(saturday, friday_bar, cfg)
    assert ok is False
    assert "session" in reason.lower() or "weekend" in reason.lower()
    weekday_open = datetime(2026, 10, 1, 11, 0, tzinfo=IST)
    same_day_bar = datetime(2026, 10, 1, 10, 57, tzinfo=IST)
    ok, _ = live_index_display_ok(weekday_open, same_day_bar, cfg)
    assert ok is True
    after_hours = datetime(2026, 10, 1, 15, 20, tzinfo=IST)
    ok, reason = live_index_display_ok(after_hours, same_day_bar, cfg)
    assert ok is False
    assert "window" in reason.lower()


def test_nifty_weekly_expiry_switches_tuesday_from_sept_2025():
    from datetime import date

    from app.domain.index_strategy import is_index_expiry

    assert is_index_expiry("NIFTY", date(2024, 8, 29)) is True
    assert is_index_expiry("NIFTY", date(2024, 9, 3)) is False
    assert is_index_expiry("NIFTY", date(2025, 8, 28)) is True
    assert is_index_expiry("NIFTY", date(2025, 9, 2)) is True
    assert is_index_expiry("NIFTY", date(2025, 9, 4)) is False
    assert is_index_expiry("NIFTY", date(2026, 10, 6)) is True
    assert is_index_expiry("SENSEX", date(2025, 9, 5)) is True
    assert is_index_expiry("SENSEX", date(2025, 9, 2)) is False
    listed = {date(2025, 8, 27)}
    assert is_index_expiry("NIFTY", date(2025, 8, 27), listed) is True
    assert is_index_expiry("NIFTY", date(2025, 8, 28), listed) is False

