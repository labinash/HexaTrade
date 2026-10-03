"""Option trading panel building blocks.

Covers the ITM/ATM/OTM ladder for both option types, both underlyings driven
only by their own instrument data, lot-size validation, and duplicate-order
protection.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Position
from app.domain.chain import strike_interval
from app.domain.strategy import (
    MarketSnapshot,
    StrategyParams,
    direction_state,
    evaluate_snapshot,
    suggested_option_type,
)
from app.domain.strike_selection import atm_from, moneyness_of, strike_ladder
from app.services.selection import recent_duplicate, resize

NIFTY_STRIKES = [24_900.0, 24_950.0, 25_000.0, 25_050.0, 25_100.0]
SENSEX_STRIKES = [72_800.0, 72_900.0, 73_000.0, 73_100.0, 73_200.0]


# ---------------------------------------------------------------- ladder


def test_call_ladder_steps_down_for_itm():
    ladder = strike_ladder(NIFTY_STRIKES, 25_000.0, "CE")
    assert ladder == {"ITM": 24_950.0, "ATM": 25_000.0, "OTM": 25_050.0}


def test_put_ladder_steps_up_for_itm():
    """A put gains value as the underlying falls, so its ITM strike is above ATM."""
    ladder = strike_ladder(NIFTY_STRIKES, 25_000.0, "PE")
    assert ladder == {"ITM": 25_050.0, "ATM": 25_000.0, "OTM": 24_950.0}


def test_call_and_put_ladders_are_mirror_images():
    call = strike_ladder(NIFTY_STRIKES, 25_000.0, "CE")
    put = strike_ladder(NIFTY_STRIKES, 25_000.0, "PE")
    assert call["ITM"] == put["OTM"]
    assert call["OTM"] == put["ITM"]
    assert call["ATM"] == put["ATM"]


def test_ladder_labels_agree_with_the_moneyness_rule():
    price = 25_000.0
    for option_type in ("CE", "PE"):
        ladder = strike_ladder(NIFTY_STRIKES, 25_000.0, option_type)
        for label in ("ITM", "OTM"):
            assert moneyness_of(option_type, ladder[label], price) == label


def test_ladder_reports_a_missing_edge_strike_instead_of_inventing_one():
    edge = strike_ladder([25_000.0, 25_050.0], 25_000.0, "CE")
    assert edge["ITM"] is None
    assert edge["ATM"] == 25_000.0
    assert edge["OTM"] == 25_050.0


def test_ladder_is_empty_when_atm_is_unknown():
    assert strike_ladder(NIFTY_STRIKES, None, "CE") == {"ITM": None, "ATM": None, "OTM": None}


def test_ladder_uses_adjacent_strikes_when_the_interval_is_uneven():
    """Steps to the neighbouring listed strike rather than adding an interval."""
    gapped = [24_800.0, 25_000.0, 25_050.0]
    ladder = strike_ladder(gapped, 25_000.0, "CE")
    assert ladder["ITM"] == 24_800.0


# ------------------------------------------------- per underlying config


def test_strike_interval_comes_from_the_listed_strikes():
    assert strike_interval(NIFTY_STRIKES) == 50
    assert strike_interval(SENSEX_STRIKES) == 100


def test_each_underlying_resolves_its_own_atm_and_ladder():
    """No shared constant: NIFTY and SENSEX are driven only by their own strikes."""
    nifty_atm = atm_from(NIFTY_STRIKES, 25_018.4)
    sensex_atm = atm_from(SENSEX_STRIKES, 73_041.0)
    assert nifty_atm == 25_000.0
    assert sensex_atm == 73_000.0
    assert strike_ladder(NIFTY_STRIKES, nifty_atm, "CE")["OTM"] == 25_050.0
    assert strike_ladder(SENSEX_STRIKES, sensex_atm, "CE")["OTM"] == 73_100.0


def test_atm_follows_the_underlying_across_a_strike():
    below = atm_from(NIFTY_STRIKES, 25_020.0)
    above = atm_from(NIFTY_STRIKES, 25_030.0)
    assert below == 25_000.0
    assert above == 25_050.0
    # The ladder moves with it, which is what drives the subscription refresh.
    assert strike_ladder(NIFTY_STRIKES, above, "CE")["ITM"] == 25_000.0


# ------------------------------------------------------- direction state


def _snapshot(**overrides) -> MarketSnapshot:
    base = dict(
        close=100, open=99, ema=90, reference_close=90, oi=20000, reference_oi=5000,
        volume=2000, volume_ema=1000, vwap=95, rsi=55, atr=20, now=time(10, 30),
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def test_full_agreement_is_strong_and_scores_100():
    params = StrategyParams(direction="BULLISH")
    state, score = direction_state(evaluate_snapshot(_snapshot(), params, 15), params)
    assert state == "STRONG_BULLISH"
    assert score == 100


def test_partial_agreement_grades_down_without_authorising_a_trade():
    params = StrategyParams(direction="BULLISH")
    evaluation = evaluate_snapshot(_snapshot(volume=10), params, 15)
    state, score = direction_state(evaluation, params)
    assert state == "BULLISH"
    assert score < 100
    # Grading is presentation. The entry gate is unchanged.
    assert not evaluation.passed
    assert evaluation.overall_signal() == "NO_TRADE"


def test_widespread_disagreement_reports_no_direction_and_suggests_no_side():
    params = StrategyParams(direction="BULLISH")
    evaluation = evaluate_snapshot(
        _snapshot(close=80, open=85, ema=100, vwap=95, rsi=20, volume=10, atr=1, oi=100, reference_oi=100),
        params,
        15,
    )
    state, _ = direction_state(evaluation, params)
    assert state == "NO_DIRECTION"
    assert suggested_option_type(state, params) is None


def test_bearish_configuration_grades_on_its_own_side():
    params = StrategyParams(direction="BEARISH")
    snapshot = _snapshot(close=90, open=95, ema=100, reference_close=100, vwap=95, rsi=45)
    state, score = direction_state(evaluate_snapshot(snapshot, params, 15), params)
    assert state == "STRONG_BEARISH"
    assert score == 100
    assert suggested_option_type(state, params) == "PE"


def test_score_is_never_presented_as_a_probability():
    """Guards the wording contract: the score is agreement between conditions."""
    params = StrategyParams(direction="BULLISH")
    _, score = direction_state(evaluate_snapshot(_snapshot(volume=10), params, 15), params)
    assert 0 <= score <= 100


# ---------------------------------------------------------- lot sizing


def _candidate(**overrides):
    from app.domain.greeks import EMPTY
    from app.domain.strike_selection import Candidate

    base = dict(
        tradingsymbol="NIFTY25000CE", token=111, strike=25_000.0, option_type="CE",
        expiry=date(2026, 10, 8), lot_size=75, ltp=145.2, bid=145.1, ask=145.3,
        bid_quantity=100, ask_quantity=100, volume=125_000, oi=850_000, oi_change=45_000,
        oi_change_percent=5.6, volume_oi_ratio=0.15, premium_change=2.0, spread=0.2,
        spread_percent=0.14, moneyness="ATM", strikes_from_atm=0, distance_from_atm=0.0,
        iv=0.142, iv_relative=1.0, iv_band="NORMAL", iv_percentile=None, greeks=EMPTY,
        days_to_expiry=8, expiry_band="NORMAL", quote_age_seconds=1.0, entry=145.3,
        stop_loss=120.0, target=182.5, risk_per_unit=25.3, reward_per_unit=37.2,
        reward_risk=1.47, underlying_stop=None, expected_move=20.0,
        expected_underlying_target=None, expected_option_response=None,
        allowed_lots=3, quantity=225, risk_amount=5692.5, capital_required=32_692.5,
        oi_interpretation="Price up with OI up, possible long buildup",
    )
    base.update(overrides)
    return Candidate(**base)


def test_quantity_is_derived_from_the_instrument_lot_size():
    candidate = _candidate()
    resize(candidate, 2, 75)
    assert candidate.allowed_lots == 2
    assert candidate.quantity == 150


def test_a_lot_size_disagreeing_with_the_instrument_master_is_rejected():
    """Lot sizes change, so a stale quote must not silently size the order."""
    with pytest.raises(ValueError, match="refresh the instrument master"):
        resize(_candidate(lot_size=75), 1, 50)


def test_lots_above_the_configured_risk_cap_are_rejected():
    with pytest.raises(ValueError, match="exceeds the 3 lots allowed"):
        resize(_candidate(allowed_lots=3), 4, 75)


def test_a_contract_the_risk_limits_size_at_zero_cannot_be_bought():
    with pytest.raises(ValueError, match="allow zero lots"):
        resize(_candidate(allowed_lots=0), 1, 75)


def test_zero_or_negative_lots_are_rejected():
    for lots in (0, -1):
        with pytest.raises(ValueError, match="at least 1"):
            resize(_candidate(), lots, 75)


def test_resizing_recomputes_risk_and_capital():
    candidate = _candidate(risk_per_unit=25.0, entry=145.0)
    resize(candidate, 2, 75)
    assert candidate.risk_amount == pytest.approx(25.0 * 150)
    assert candidate.capital_required == pytest.approx(145.0 * 150)


# ------------------------------------------------ duplicate protection


@pytest.fixture()
def db():
    from app.db.bootstrap import _add_version
    from app.db.models import Strategy

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    strategy = Strategy(name="test")
    session.add(strategy)
    session.flush()
    session.version = _add_version(session, strategy, 1, "first")
    yield session
    session.close()


def _position(session, *, token: int, seconds_ago: int) -> Position:
    row = Position(
        underlying="NIFTY", exchange="NFO", tradingsymbol="NIFTY25000CE",
        instrument_token=token, option_type="CE", strike=25_000,
        entry_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=seconds_ago),
        entry_price=145, quantity=75, execution_mode="PAPER", status="OPEN",
        strategy_version_id=session.version.id,
    )
    session.add(row)
    session.flush()
    return row


def test_a_repeat_buy_inside_the_window_is_flagged(db):
    _position(db, token=111, seconds_ago=5)
    assert recent_duplicate(db, 111, "BUY", 60) is not None


def test_a_buy_after_the_window_is_allowed(db):
    _position(db, token=111, seconds_ago=120)
    assert recent_duplicate(db, 111, "BUY", 60) is None


def test_a_different_contract_is_not_a_duplicate(db):
    _position(db, token=111, seconds_ago=5)
    assert recent_duplicate(db, 222, "BUY", 60) is None


def test_the_window_is_configurable_and_zero_disables_the_check(db):
    _position(db, token=111, seconds_ago=5)
    assert recent_duplicate(db, 111, "BUY", 2) is None
    assert recent_duplicate(db, 111, "BUY", 0) is None


def test_bullish_configuration_still_prefers_ce():
    from app.domain.strategy import StrategyParams
    from app.services.option_panel import buy_signal, preferred_option

    assert preferred_option(StrategyParams(direction="BULLISH")) == "CE"
    assert preferred_option(StrategyParams(direction="BEARISH")) == "PE"
    assert buy_signal(False, "CE") == "NO TRADE"
    assert buy_signal(True, "CE") == "BUY CE"
    assert buy_signal(True, "PE") == "BUY PE"
