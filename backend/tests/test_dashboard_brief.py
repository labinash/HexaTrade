"""Dashboard briefing explains existing evaluations. It does not trade."""

from datetime import time

from app.domain.dashboard_brief import (
    NOT_READY,
    READY,
    WAITING,
    candidate_label,
    confirmed_trend,
    contract_gaps,
    dashboard_decision,
    direction_basis,
    implied_market_direction,
    indicator_summary,
    market_status,
    pick_best,
    preferred_label,
    preferred_side_for,
    readiness_copy,
    score_percent,
)
from app.domain.option_buy import OptionStrategyResult


def test_score_percent_normalises_unequal_totals():
    assert score_percent(7, 9) == 77.78
    assert score_percent(5, 11) == 45.45
    assert score_percent(7, 9) > score_percent(5, 11)


def test_pick_best_uses_normalized_score_not_raw_counts():
    ce = {"option_type": "CE", "strike": 22650, "score_percent": 77.78, "passed_count": 7, "atm_distance": 50}
    pe = {"option_type": "PE", "strike": 22600, "score_percent": 72.73, "passed_count": 8, "atm_distance": 0}
    best, reason = pick_best([ce, pe], 60)
    assert best is ce
    assert reason == ""


def test_pick_best_respects_minimum_threshold():
    ce = {"option_type": "CE", "score_percent": 44.44, "passed_count": 4, "atm_distance": 0}
    pe = {"option_type": "PE", "score_percent": 36.36, "passed_count": 4, "atm_distance": 0}
    best, reason = pick_best([ce, pe], 60)
    assert best is None
    assert "threshold" in reason


def test_candidate_label_never_calls_incomplete_a_buy():
    assert candidate_label(passed=True, score=100, threshold=60) == "SETUP VALID"
    assert candidate_label(passed=False, score=77.8, threshold=60) == "WATCH"
    assert candidate_label(passed=False, score=44, threshold=60) == "BELOW THRESHOLD"


def test_market_status_ready_is_not_a_buy_gate():
    kwargs = dict(
        clock=time(10, 30),
        session_start=time(9, 15),
        session_end=time(15, 30),
        time_start=time(9, 30),
        time_end=time(15, 0),
        time_enabled=True,
        stale=False,
        has_futures=True,
        has_spot=True,
    )
    assert market_status(**kwargs) == READY
    assert market_status(**{**kwargs, "stale": True}) == NOT_READY
    assert market_status(**{**kwargs, "clock": time(8, 0)}) == NOT_READY
    assert market_status(**{**kwargs, "clock": time(9, 20), "has_futures": True}) == WAITING
    assert market_status(**{**kwargs, "has_futures": False, "has_spot": False}) == WAITING


def test_direction_basis_uses_existing_condition_rows():
    rows = direction_basis(
        [
            {"name": "EMA Trend", "status": "PASS", "current": "22,625", "required": "Close > EMA20", "is_directional": True},
            {"name": "VWAP", "status": "FAIL", "current": "Below", "required": "Close > VWAP", "is_directional": True},
        ]
    )
    assert rows[0]["short_name"] == "EMA"
    assert rows[0]["passed"] is True
    assert rows[1]["short_name"] == "VWAP"
    assert rows[1]["passed"] is False


def test_readiness_copy_does_not_auto_select_a_trade():
    assert "No strategy" in readiness_copy(False, False)
    assert readiness_copy(True, False).startswith("CE setup")
    assert readiness_copy(False, True).startswith("PE setup")
    assert readiness_copy(True, True).startswith("Both setups")


def test_implied_direction_uses_live_tape_not_configured_bias():
    assert (
        implied_market_direction(close=22443, ema=22438, vwap=22547, momentum=-280) == "BEARISH"
    )
    assert implied_market_direction(close=22600, ema=22500, vwap=22480, momentum=40) == "BULLISH"
    assert implied_market_direction(close=22500, ema=22500, vwap=22500, momentum=0) == "NEUTRAL"


def test_implied_direction_ignores_ema_chop_of_a_few_points():
    assert implied_market_direction(close=22443, ema=22438, vwap=22547, momentum=None) == "BEARISH"


def test_option_result_dict_includes_score_percent():
    result = OptionStrategyResult(
        strategy="CE",
        status="NO TRADE",
        passed=False,
        passed_count=2,
        total_count=9,
        conditions=[],
        option_type="CE",
    )
    payload = result.as_dict()
    assert payload["score_percent"] == 22.22
    assert payload["passed_count"] == 2
    assert payload["total_count"] == 9


def test_confirmed_trend_is_not_ready_when_session_data_is_unusable():
    assert confirmed_trend("NOT READY", "BULLISH") == "NOT READY"
    assert confirmed_trend("READY", "BULLISH") == "BULLISH"
    assert confirmed_trend("READY", "BEARISH") == "BEARISH"
    assert confirmed_trend("READY", "NEUTRAL") == "NEUTRAL"


def test_preferred_side_follows_confirmed_trend_only():
    assert preferred_side_for("BULLISH") == "CE"
    assert preferred_side_for("BEARISH") == "PE"
    assert preferred_side_for("NEUTRAL") is None
    assert preferred_side_for("NOT READY") is None
    assert preferred_label("BULLISH") == "PREFERRED: CE — BULLISH TREND"
    assert preferred_label("BEARISH") == "PREFERRED: PE — BEARISH TREND"
    assert "NO PREFERRED CONTRACT" in preferred_label("NEUTRAL")


def test_trend_alone_is_never_a_buy():
    watch = dashboard_decision(
        trend="BULLISH",
        market_status="READY",
        side="CE",
        result={"passed": False, "score_percent": 80},
        enabled=True,
        threshold=75,
        gaps=[],
        missing_notes=[],
    )
    assert watch["buy"] is False
    assert watch["label"] == "CE WATCH"
    buy = dashboard_decision(
        trend="BULLISH",
        market_status="READY",
        side="CE",
        result={"passed": True, "score_percent": 88},
        enabled=True,
        threshold=75,
        gaps=[],
        missing_notes=[],
    )
    assert buy["label"] == "BUY"
    assert buy["buy"] is True
    ready = dashboard_decision(
        trend="NOT READY",
        market_status="NOT READY",
        side=None,
        result=None,
        enabled=True,
        threshold=75,
        gaps=[],
        missing_notes=["stale"],
    )
    assert ready["label"] == "DATA NOT READY"
    assert ready["buy"] is False
    neutral = dashboard_decision(
        trend="NEUTRAL",
        market_status="READY",
        side=None,
        result=None,
        enabled=True,
        threshold=75,
        gaps=[],
        missing_notes=[],
    )
    assert neutral["label"] == "NO TRADE"
    assert "NO PREFERRED CONTRACT" in neutral["reason"]


def test_indicator_summary_counts_only_applicable_rows():
    summary = indicator_summary(
        [
            {"name": "EMA Trend", "status": "PASS", "passed": True},
            {"name": "Momentum", "status": "FAIL", "passed": False},
            {"name": "VWAP", "status": "PASS", "passed": True},
        ],
        [
            {"name": "Premium Trend", "status": "DATA UNAVAILABLE", "passed": False},
            {"name": "Premium Candle", "status": "PASS", "passed": True},
        ],
    )
    assert summary["green"] == 3
    assert summary["total"] == 4
    assert summary["text"] == "Green indicators: 3 / 4"
    assert "66.7" in summary["percent_text"] or summary["percent"] == 75.0
    ema = next(row for row in summary["items"] if row["name"] == "EMA")
    prem = next(row for row in summary["items"] if row["name"] == "Premium trend")
    assert ema["state"] == "PASS" and ema["mark"] == "✓"
    assert prem["state"] == "NA"


def test_contract_gaps_require_quote_fields():
    assert "selected option contract" in contract_gaps(None)
    missing = contract_gaps({"tradingsymbol": "NIFTY25OCT25000CE", "strike": 25000})
    assert "expiry" in missing
    assert "premium LTP" in missing
    assert "bid/ask spread" in missing
