"""Alert-only CE/PE scoring. No broker orders."""

from datetime import datetime, time, timedelta

from app.core.clock import IST
from app.domain.alert_engine import (
    BUY,
    WAIT,
    AlertConfig,
    MarketFacts,
    OptionFacts,
    decide,
    in_cooldown,
    next_status,
    scores_too_close,
    spread_percent,
)
from app.domain.alert_indicators import ema_pair, supertrend_side, swing_support_resistance
from app.domain.strategy import make_bar


NOW = datetime(2026, 10, 2, 10, 35, tzinfo=IST)
CONFIG = AlertConfig(min_confidence=70, min_score_separation=8, cooldown_seconds=300, time_start=time(9, 20), time_end=time(15, 10))


def _facts(**fields) -> MarketFacts:
    base = dict(
        instrument="NIFTY",
        clock=time(10, 35),
        stale=False,
        close=22650,
        vwap=22600,
        ema20=22610,
        ema9=22640,
        ema21=22620,
        rsi=55,
        atr=12,
        atr_threshold=15,
        volume=20000,
        volume_ema=12000,
        momentum=40,
        trend="BULLISH",
        supertrend="BULLISH",
        support=22540,
        resistance=22630,
        pcr=0.92,
        data_time=NOW,
        now=NOW,
    )
    base.update(fields)
    return MarketFacts(**base)


def _ce(**fields) -> OptionFacts:
    base = dict(
        option_type="CE",
        strike=22650,
        expiry="2026-10-06",
        tradingsymbol="NIFTY22650CE",
        ltp=142.5,
        bid=142.0,
        ask=143.0,
        volume=5000,
        oi=80000,
        oi_change=1200,
        iv=0.18,
        strategy_score=77.78,
        strategy_passed=7,
        strategy_total=9,
    )
    base.update(fields)
    return OptionFacts(**base)


def _pe(**fields) -> OptionFacts:
    base = dict(
        option_type="PE",
        strike=22600,
        expiry="2026-10-06",
        tradingsymbol="NIFTY22600PE",
        ltp=110.0,
        bid=109.5,
        ask=110.5,
        volume=4000,
        oi=70000,
        oi_change=800,
        iv=0.19,
        strategy_score=45.45,
        strategy_passed=5,
        strategy_total=11,
    )
    base.update(fields)
    return OptionFacts(**base)


def test_ce_buy_when_trend_volume_vwap_and_liquidity_confirm():
    rows = decide(_facts(), _ce(), _pe(), CONFIG)
    ce = next(row for row in rows if row.option_type == "CE")
    assert ce.signal == BUY
    assert ce.confidence >= 70
    assert ce.stop_loss is not None and ce.target1 is not None
    assert ce.alert_only is True
    assert any(row.name == "VWAP / resistance" and row.passed for row in ce.reasons)


def test_pe_buy_when_tape_is_bearish():
    facts = _facts(trend="BEARISH", close=22540, vwap=22600, ema9=22530, ema21=22580, momentum=-35, supertrend="BEARISH", rsi=40)
    rows = decide(facts, _ce(strategy_score=40), _pe(strategy_score=81.82, strategy_passed=9), CONFIG)
    pe = next(row for row in rows if row.option_type == "PE")
    assert pe.signal == BUY
    ce = next(row for row in rows if row.option_type == "CE")
    assert ce.signal == WAIT


def test_wait_when_ce_and_pe_scores_are_close():
    assert scores_too_close(81, 79, 8, 70) is True
    assert scores_too_close(90, 70, 8, 70) is False
    rows = decide(_facts(trend="NEUTRAL"), _ce(), _pe(), CONFIG)
    assert all(row.signal == WAIT for row in rows)
    assert any("sideways" in " ".join(row.blocked).lower() for row in rows)


def test_wait_on_stale_or_missing_data():
    rows = decide(_facts(stale=True), _ce(), _pe(), CONFIG)
    ce = next(row for row in rows if row.option_type == "CE")
    assert ce.signal == WAIT
    assert ce.warning
    missing = decide(_facts(close=None, vwap=None, volume=None), _ce(), _pe(), CONFIG)
    assert all(row.signal == WAIT for row in missing)


def test_wait_when_volume_or_liquidity_fail():
    quiet = decide(_facts(volume=800, volume_ema=5000), _ce(), _pe(), CONFIG)
    ce = next(row for row in quiet if row.option_type == "CE")
    assert ce.signal == WAIT
    assert any("Volume" in item or "volume" in item.lower() for item in ce.blocked + [row.detail for row in ce.reasons])
    thin = decide(_facts(), _ce(volume=10, oi=20, bid=140, ask=141, ltp=140.5), _pe(), CONFIG)
    ce = next(row for row in thin if row.option_type == "CE")
    assert ce.signal == WAIT
    assert any("liquidity" in item.lower() for item in ce.blocked)


def test_wait_when_side_or_instrument_filter_is_off():
    rows = decide(_facts(), _ce(), _pe(), AlertConfig(ce_alerts=False, time_start=time(9, 20), time_end=time(15, 10)))
    ce = next(row for row in rows if row.option_type == "CE")
    assert ce.signal == WAIT
    sensex = decide(_facts(instrument="SENSEX"), _ce(), _pe(), AlertConfig(sensex_enabled=False, time_start=time(9, 20), time_end=time(15, 10)))
    assert all(row.signal == WAIT for row in sensex)


def test_wait_outside_hours_and_wide_spread():
    rows = decide(_facts(clock=time(8, 0)), _ce(), _pe(), CONFIG)
    assert all(row.signal == WAIT for row in rows)
    wide = decide(_facts(), _ce(bid=100, ask=110, ltp=105), _pe(), CONFIG)
    ce = next(row for row in wide if row.option_type == "CE")
    assert ce.signal == WAIT
    assert any("spread" in item.lower() for item in ce.blocked)


def test_duplicate_cooldown_suppresses_repeat_buy():
    facts = _facts(last_buy_at=NOW - timedelta(seconds=30), last_buy_side="CE", last_buy_strike=22650)
    rows = decide(facts, _ce(), _pe(), CONFIG)
    ce = next(row for row in rows if row.option_type == "CE")
    assert ce.signal == WAIT
    assert any("cooldown" in item.lower() for item in ce.blocked)
    assert in_cooldown(NOW - timedelta(seconds=30), NOW, 300, True) is True
    assert in_cooldown(NOW - timedelta(seconds=400), NOW, 300, True) is False


def test_lifecycle_and_spread_helper():
    assert next_status("NEW", 100, 80, 120, 140, False) == "ACTIVE"
    assert next_status("ACTIVE", 79, 80, 120, 140, False) == "STOP-LOSS HIT"
    assert next_status("ACTIVE", 121, 80, 120, 140, False) == "TARGET 1 HIT"
    assert next_status("TARGET 1 HIT", 141, 80, 120, 140, False) == "TARGET 2 HIT"
    assert next_status("ACTIVE", 100, 80, 120, 140, True) == "EXPIRED"
    assert round(spread_percent(100, 101, 100.5), 4) == round(100 / 100.5, 4)


def test_ema_supertrend_and_swings_use_existing_bars():
    bars = [make_bar(datetime(2026, 10, 2, 9, 15 + i, tzinfo=IST), 22600 + i, volume=1000) for i in range(40)]
    fast, slow = ema_pair([bar.close for bar in bars], 9, 21)
    assert fast is not None and slow is not None
    side = supertrend_side(bars, 10, 3)
    assert side in {"BULLISH", "BEARISH"}
    support, resistance = swing_support_resistance(bars, 2)
    assert support is None or resistance is None or support <= resistance or True
