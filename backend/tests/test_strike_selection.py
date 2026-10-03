from datetime import date, datetime, timedelta

from app.domain.greeks import black76_greeks
from app.domain.strike_selection import (
    OptionQuote,
    ScoreWeights,
    SelectionConfig,
    UnderlyingInput,
    candidate_strikes,
    evaluate,
    oi_interpretation,
    option_type_for,
    position_size,
)

NOW = datetime(2026, 9, 29, 10, 30)
EXPIRY = date(2026, 10, 8)


def config(**overrides) -> SelectionConfig:
    base = dict(
        strikes_each_side=2,
        min_volume=100,
        min_oi=1000,
        max_spread_percent=5,
        max_quote_age_seconds=10,
        min_premium=10,
        max_premium=500,
        min_reward_risk=1.5,
        min_score=40,
        account_capital=2000000,
        max_capital_allocation=2000000,
        max_daily_loss=100000,
    )
    base.update(overrides)
    return SelectionConfig(**base)


def underlying(**overrides) -> UnderlyingInput:
    base = dict(
        symbol="NIFTY",
        direction="BULLISH",
        signal_valid=True,
        conditions_passed=8,
        conditions_failed=0,
        spot=24500.0,
        futures_ltp=24520.0,
        futures_volume=100000.0,
        futures_oi=1000000.0,
        futures_oi_change=20000.0,
        vwap=24480.0,
        ema=24460.0,
        rsi=61.0,
        atr=42.0,
        reference_close=24500.0,
        reference_oi=None,
        strike_interval=50.0,
        stale=False,
    )
    base.update(overrides)
    return UnderlyingInput(**base)


def quote(strike: float, option_type: str = "CE", **overrides) -> OptionQuote:
    base = dict(
        token=int(strike),
        tradingsymbol=f"NIFTY26O08{int(strike)}{option_type}",
        strike=strike,
        option_type=option_type,
        expiry=EXPIRY,
        lot_size=75,
        ltp=150.0,
        bid=149.5,
        ask=150.5,
        bid_quantity=1500,
        ask_quantity=1800,
        volume=50000,
        oi=200000,
        day_open_oi=180000,
        change=12.0,
        iv=0.14,
        quote_age_seconds=1.0,
    )
    base.update(overrides)
    return OptionQuote(**base)


def chain(**overrides) -> list[OptionQuote]:
    return [quote(strike, **overrides) for strike in (24400, 24450, 24500, 24550, 24600)]


def test_bullish_maps_to_call_only_and_bearish_to_put_only():
    cfg = config()
    assert option_type_for("BULLISH", cfg) == "CE"
    assert option_type_for("BEARISH", cfg) == "PE"
    assert option_type_for("NONE", cfg) is None

    mixed = chain() + [quote(24500, "PE", token=99)]
    result = evaluate(underlying(), mixed, cfg, NOW)
    assert result.option_type == "CE"
    assert {c.option_type for c in result.all_candidates} == {"CE"}


def test_strike_window_uses_interval_from_instrument_master():
    strikes = [24300, 24400, 24500, 24600, 24700]
    assert candidate_strikes(strikes, 24500, 1) == [24400, 24500, 24600]
    result = evaluate(underlying(), chain(), config(strikes_each_side=1), NOW)
    assert len(result.all_candidates) == 3
    assert result.atm == 24500
    assert result.strike_interval == 50.0


def test_illiquid_strikes_are_rejected_with_reasons():
    quotes = chain()
    quotes[2] = quote(24500, volume=10, oi=50)
    result = evaluate(underlying(), quotes, config(), NOW)
    rejected = {c.tradingsymbol: c for c in result.rejected}
    target = next(c for c in result.all_candidates if c.strike == 24500)
    assert target.status == "REJECT"
    assert target.tradingsymbol in rejected
    assert any("volume" in reason for reason in target.reject_reasons)
    assert any("OI" in reason for reason in target.reject_reasons)


def test_stale_quote_is_rejected_and_never_treated_as_zero():
    quotes = chain(quote_age_seconds=45.0)
    result = evaluate(underlying(), quotes, config(max_quote_age_seconds=10), NOW)
    assert result.selected is None
    assert all(c.status == "REJECT" for c in result.all_candidates)
    assert any("quote age" in reason for c in result.all_candidates for reason in c.reject_reasons)


def test_missing_quote_fields_do_not_become_zero():
    quotes = chain()
    quotes[2] = quote(24500, ltp=None, bid=None, ask=None, volume=None, oi=None)
    result = evaluate(underlying(), quotes, config(), NOW)
    target = next(c for c in result.all_candidates if c.strike == 24500)
    assert target.status == "REJECT"
    assert target.ltp is None and target.volume is None and target.oi is None
    assert target.spread_percent is None


def test_no_suitable_option_when_every_candidate_fails():
    result = evaluate(underlying(), chain(volume=1, oi=1), config(), NOW)
    assert result.available is True
    assert result.selected is None
    assert result.top == []
    assert result.no_trade_reasons


def test_selection_is_not_forced_when_best_score_is_below_minimum():
    result = evaluate(underlying(), chain(), config(min_score=99.9), NOW)
    assert result.selected is None
    assert result.top, "candidates still shown for transparency"
    assert "below the configured minimum" in result.no_trade_reasons[0]


def test_invalid_signal_blocks_the_engine():
    result = evaluate(underlying(signal_valid=False), chain(), config(), NOW)
    assert result.available is False
    assert result.selected is None
    assert "strategy signal is not valid" in result.no_trade_reasons


def test_stale_underlying_blocks_the_engine():
    result = evaluate(underlying(stale=True), chain(), config(), NOW)
    assert result.available is False
    assert "underlying market data is stale" in result.no_trade_reasons


def test_score_is_normalised_between_zero_and_hundred():
    result = evaluate(underlying(), chain(), config(), NOW)
    assert result.top
    for candidate in result.top:
        assert 0 <= candidate.score <= 100
        assert sum(candidate.subscores.values()) > 0


def test_hard_filters_are_independent_of_weights():
    zero_weight = config(weights=ScoreWeights(liquidity=0, spread=0, delta=0, reward_risk=0, volume_oi=0, iv=0, premium=0, expiry=0, atm_distance=1))
    quotes = chain()
    quotes[0] = quote(24400, volume=1)
    result = evaluate(underlying(), quotes, zero_weight, NOW)
    weak = next(c for c in result.all_candidates if c.strike == 24400)
    assert weak.status == "REJECT", "a soft weight of zero must not rescue a hard-filter failure"


def test_top_three_candidates_are_returned_in_score_order():
    result = evaluate(underlying(), chain(), config(), NOW)
    assert len(result.top) <= 3
    scores = [c.score for c in result.top]
    assert scores == sorted(scores, reverse=True)


def test_reward_risk_below_minimum_is_rejected():
    result = evaluate(underlying(), chain(), config(min_reward_risk=5.0, target_reward_risk=2.0), NOW)
    assert result.selected is None
    assert any("reward/risk" in reason for c in result.all_candidates for reason in c.reject_reasons)


def test_deep_otm_outside_delta_range_is_rejected():
    far = quote(26000, ltp=12.0, bid=11.8, ask=12.2, iv=0.18)
    result = evaluate(underlying(), chain() + [far], config(strikes_each_side=20), NOW)
    deep = next(c for c in result.all_candidates if c.strike == 26000)
    assert deep.greeks.abs_delta is not None and deep.greeks.abs_delta < 0.25
    assert deep.status == "REJECT"
    assert any("delta" in reason for reason in deep.reject_reasons)


def test_premium_outside_range_is_rejected_both_ways():
    cheap = evaluate(underlying(), chain(ltp=5.0, bid=4.9, ask=5.1), config(min_premium=10), NOW)
    assert any("below minimum" in reason for c in cheap.all_candidates for reason in c.reject_reasons)
    rich = evaluate(underlying(), chain(ltp=900.0, bid=899, ask=901), config(max_premium=500), NOW)
    assert any("above maximum" in reason for c in rich.all_candidates for reason in c.reject_reasons)


def test_oi_interpretation_is_labelled_as_possible_not_certain():
    assert "possible long buildup" in oi_interpretation(10, 5000)
    assert "possible short buildup" in oi_interpretation(-10, 5000)
    assert "possible short covering" in oi_interpretation(10, -5000)
    assert "possible long unwinding" in oi_interpretation(-10, -5000)
    assert "incomplete" in oi_interpretation(None, 5000)
    assert "incomplete" in oi_interpretation(10, None)


def test_same_day_expiry_is_flagged_as_a_warning():
    today = chain()
    same_day = [
        OptionQuote(**{**vars(q), "expiry": NOW.date()}) for q in today
    ]
    result = evaluate(underlying(), same_day, config(), NOW)
    flagged = [c for c in result.all_candidates if c.expiry_band == "SAME_DAY"]
    assert flagged
    assert all(any("time-decay" in w or "time decay" in w for w in c.warnings) for c in flagged)


def test_expected_move_uses_configured_atr_multiplier():
    result = evaluate(underlying(atr=40.0), chain(), config(atr_multiplier=1.5), NOW)
    assert result.expected_move == 60.0


def test_position_size_respects_risk_and_capital_caps():
    small = config(account_capital=100000, max_risk_percent=1, max_lots=10)
    assert position_size(150.0, 120.0, 75, small, 0.0)[0] == 0, "1% of 100000 is below the risk of one lot"
    roomy = config(account_capital=1000000, max_risk_percent=1, max_lots=10)
    lots, risk, capital = position_size(150.0, 120.0, 75, roomy, 0.0)
    assert lots == 4 and risk == 30 * 75 * 4 and capital == 150 * 75 * 4
    capped, _, _ = position_size(150.0, 120.0, 75, config(account_capital=1000000, max_risk_percent=1, max_lots=2), 0.0)
    assert capped == 2
    allocation_capped, _, _ = position_size(150.0, 120.0, 75, config(account_capital=1000000, max_risk_percent=1, max_lots=10, max_capital_allocation=25000), 0.0)
    assert allocation_capped == 2


def test_daily_loss_already_taken_shrinks_position_size():
    cfg = config(account_capital=1000000, max_risk_percent=1, max_lots=10, max_capital_allocation=1000000, max_daily_loss=5000)
    fresh, _, _ = position_size(150.0, 120.0, 75, cfg, 0.0)
    used, _, _ = position_size(150.0, 120.0, 75, cfg, 4000.0)
    assert used < fresh


def test_black76_call_and_put_delta_bounds():
    call = black76_greeks(24520, 24500, 10 / 365, 0.07, 0.14, True)
    put = black76_greeks(24520, 24500, 10 / 365, 0.07, 0.14, False)
    assert 0 < call.delta < 1
    assert -1 < put.delta < 0
    assert call.gamma and call.gamma > 0
    assert call.theta_per_day is not None and call.theta_per_day < 0
    assert call.vega_per_point and call.vega_per_point > 0


def test_greeks_unavailable_without_implied_vol():
    empty = black76_greeks(24520, 24500, 10 / 365, 0.07, None, True)
    assert empty.delta is None and empty.abs_delta is None


def test_expired_contract_returns_no_greeks():
    assert black76_greeks(24520, 24500, 0.0, 0.07, 0.14, True).delta is None


def test_selected_candidate_carries_reasons_and_warnings():
    result = evaluate(underlying(), chain(), config(), NOW)
    assert result.selected is not None
    assert result.selected.reasons
    assert result.selected.stop_loss < result.selected.entry < result.selected.target
    assert "not a probability of profit" in result.disclaimer


def test_zero_lot_sizing_is_a_hard_rejection():
    tiny = config(account_capital=1000, max_risk_percent=1, max_capital_allocation=1000)
    result = evaluate(underlying(), chain(), tiny, NOW)
    assert result.selected is None
    assert any("zero lots" in reason for c in result.all_candidates for reason in c.reject_reasons)


def test_high_iv_can_warn_or_reject_by_configuration():
    quotes = chain()
    quotes[2] = quote(24500, iv=0.40)
    warned = evaluate(underlying(), quotes, config(iv_high_action="warn"), NOW)
    target = next(c for c in warned.all_candidates if c.strike == 24500)
    assert target.iv_band == "HIGH"
    rejected = evaluate(underlying(), quotes, config(iv_high_action="reject"), NOW)
    strict = next(c for c in rejected.all_candidates if c.strike == 24500)
    assert strict.status == "REJECT"


def test_underlying_atr_stop_needs_delta_and_atr():
    cfg = config(stop_method="underlying_atr", stop_atr_multiplier=1.0)
    result = evaluate(underlying(), chain(), cfg, NOW)
    assert result.all_candidates
    for candidate in result.all_candidates:
        if candidate.stop_loss is not None:
            assert candidate.underlying_stop is not None
    missing = evaluate(underlying(atr=None), chain(), cfg, NOW)
    assert all(c.stop_loss is None for c in missing.all_candidates)
    assert missing.selected is None


def test_module_never_calls_a_broker():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "domain" / "strike_selection.py"
    text = source.read_text()
    assert "place_order" not in text
    assert "kiteconnect" not in text


def test_quote_age_helper_reports_none_when_timestamp_missing():
    from app.services.selection import quote_age

    assert quote_age(None) is None
    recent = datetime.now() - timedelta(seconds=3)
    age = quote_age(recent)
    assert age is not None and 0 <= age < 3600
