"""Paper auto-trade eligibility and charges. No broker orders."""

from datetime import date
from decimal import Decimal

from app.domain.charges import default_calculator, money
from app.domain.paper_auto import (
    aligned_with_tape,
    choose_eligible_candidate,
    complete_setup,
    daily_summary,
    eligible_score,
    gross_pnl,
    idempotency_key,
    index_side_for_tape,
    net_pnl,
    signed_gross,
)


def test_score_threshold_examples():
    assert eligible_score(7, 9, Decimal("75")) is True
    assert eligible_score(6, 9, Decimal("75")) is False
    assert eligible_score(8, 11, Decimal("75")) is False
    assert eligible_score(9, 11, Decimal("75")) is True


def test_paper_entry_requires_complete_setup_and_live_tape():
    watch = {"is_strategy_valid": False, "option_type": "PE", "passed_count": 9, "total_count": 11}
    valid = {"is_strategy_valid": True, "option_type": "PE", "passed_count": 11, "total_count": 11}
    assert complete_setup(watch) is False
    assert complete_setup(valid) is True
    assert aligned_with_tape("PE", "BEARISH") is True
    assert aligned_with_tape("CE", "BEARISH") is False
    assert aligned_with_tape("PE", "BULLISH") is False
    assert aligned_with_tape("CE", "NEUTRAL") is False
    assert aligned_with_tape("BUY", "BULLISH") is True
    assert aligned_with_tape("SELL", "BEARISH") is True
    assert aligned_with_tape("BUY", "BEARISH") is False


def test_higher_normalized_score_wins_not_raw_count():
    ce = {"option_type": "CE", "strike": 22650, "score_percent": 77.78, "passed_count": 7, "atm_distance": 50}
    pe = {"option_type": "PE", "strike": 22600, "score_percent": 81.82, "passed_count": 9, "atm_distance": 0}
    best, _ = choose_eligible_candidate(ce, pe, 75)
    assert best is pe


def test_below_threshold_is_not_selected():
    ce = {"option_type": "CE", "score_percent": 66.67, "passed_count": 6, "atm_distance": 0}
    pe = {"option_type": "PE", "score_percent": 72.73, "passed_count": 8, "atm_distance": 0}
    best, _ = choose_eligible_candidate(ce, pe, 75)
    assert best is None


def test_idempotency_key_is_deterministic():
    assert idempotency_key(date(2026, 10, 1), "NIFTY", "CE", 22650, "2026-10-06") == (
        "paper|2026-10-01|NIFTY|CE|22650|2026-10-06"
    )


def test_gross_and_net_use_decimal():
    gross = gross_pnl(Decimal("171.25"), Decimal("156.50"), 75)
    assert gross == Decimal("1106.25")
    loss = gross_pnl(Decimal("145.00"), Decimal("156.50"), 75)
    assert loss == Decimal("-862.50")
    assert net_pnl(gross, Decimal("40.00")) == Decimal("1066.25")


def test_zerodha_option_charges_are_decimal_and_include_sell_stt():
    calc = default_calculator()
    buy = calc.calculate_buy(Decimal("156.50"), 75)
    sell = calc.calculate_sell(Decimal("182.50"), 75)
    total = calc.calculate_round_trip(Decimal("156.50"), Decimal("182.50"), 75)
    assert isinstance(buy.total, Decimal)
    assert buy.stt == Decimal("0.00")
    assert sell.stt == money(Decimal("182.50") * 75 * Decimal("0.15") / Decimal("100"))
    assert buy.brokerage == Decimal("20.00")
    assert sell.brokerage == Decimal("20.00")
    assert total.total == buy.total + sell.total
    assert total.brokerage == Decimal("40.00")


def test_daily_summary_headline_is_net():
    summary = daily_summary(
        [
            {"status": "CLOSED", "gross_pnl": 1950, "total_charges": 80, "net_pnl": 1870},
            {"status": "OPEN", "gross_pnl": 1106.25, "total_charges": 60, "net_pnl": 1046.25},
        ]
    )
    assert summary["trades"] == 2
    assert summary["open"] == 1
    assert summary["closed"] == 1
    assert summary["net_pnl"] == Decimal("2916.25")
    assert summary["win_rate"] == Decimal("100.00")


def test_index_futures_buy_sell_follow_tape_and_invert_short_pnl():
    assert index_side_for_tape("BULLISH") == "BUY"
    assert index_side_for_tape("BEARISH") == "SELL"
    assert index_side_for_tape("NEUTRAL") is None
    assert signed_gross(Decimal("25100"), Decimal("25000"), 65, short=False) > 0
    assert signed_gross(Decimal("24900"), Decimal("25000"), 65, short=True) > 0
    assert signed_gross(Decimal("25100"), Decimal("25000"), 65, short=True) < 0


def test_index_signal_notices_fire_on_rising_edge_only():
    from app.services.paper_engine import remember_index_signal_notices

    class Runtime:
        pass

    runtime = Runtime()
    first = remember_index_signal_notices(runtime, {"NIFTY": {"decision": "BUY", "buy_score": 82, "bar_unix": 11}})
    assert len(first) == 1
    assert first[0]["side"] == "BUY"
    assert first[0]["id"] == "NIFTY|BUY|11"
    still_buy = remember_index_signal_notices(runtime, {"NIFTY": {"decision": "BUY", "buy_score": 84, "bar_unix": 11}})
    assert [row["id"] for row in still_buy] == ["NIFTY|BUY|11"]
    runtime.index_signal_notices = []
    hold = remember_index_signal_notices(runtime, {"NIFTY": {"decision": "HOLD", "buy_score": 40, "bar_unix": 12}})
    assert hold == []
    sell = remember_index_signal_notices(runtime, {"NIFTY": {"decision": "SELL", "sell_score": 81, "bar_unix": 13}})
    assert [row["side"] for row in sell] == ["SELL"]


def test_index_paper_minutes_match_chart_default():
    from app.domain.index_strategy import INDEX_CONFIRM_MINUTES, INDEX_PAPER_MINUTES
    from app.services.paper_engine import INDEX_PAPER_MINUTES as PAPER_MINUTES

    assert INDEX_PAPER_MINUTES == 3
    assert INDEX_CONFIRM_MINUTES == 5
    assert PAPER_MINUTES == 3
